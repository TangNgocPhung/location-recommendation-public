"""Enrich POIs and add source, review, preference and geofence models.

Revision ID: 0003_enrich_poi_model
Revises: 0002_seed_demo_data
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op

from app.poi_features import (
    EMBEDDING_MODEL,
    dedupe_fingerprint,
    h3_cells,
    normalize_text,
    text_embedding,
)


revision = "0003_enrich_poi_model"
down_revision = "0002_seed_demo_data"
branch_labels = None
depends_on = None


def _district(address: str) -> str | None:
    match = re.search(r"(?:Quận\s+\d+|Bình Thạnh|Phú Nhuận|TP\.\s*Thủ Đức)", address, re.I)
    return match.group(0) if match else None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        """
        ALTER TABLE pois
            ADD COLUMN normalized_name TEXT,
            ADD COLUMN normalized_address TEXT,
            ADD COLUMN opening_hours JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
            ADD COLUMN open_now BOOLEAN,
            ADD COLUMN open_now_updated_at TIMESTAMPTZ,
            ADD COLUMN price_level SMALLINT NOT NULL DEFAULT 0
                CHECK (price_level BETWEEN 0 AND 4),
            ADD COLUMN amenities JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            ADD COLUMN brand TEXT,
            ADD COLUMN district TEXT,
            ADD COLUMN city TEXT NOT NULL DEFAULT 'Hồ Chí Minh',
            ADD COLUMN country_code CHAR(2) NOT NULL DEFAULT 'VN',
            ADD COLUMN source TEXT NOT NULL DEFAULT 'seed',
            ADD COLUMN source_id TEXT,
            ADD COLUMN canonical_id UUID,
            ADD COLUMN h3_r7 TEXT,
            ADD COLUMN h3_r8 TEXT,
            ADD COLUMN h3_r9 TEXT,
            ADD COLUMN embedding REAL[],
            ADD COLUMN embedding_model TEXT,
            ADD COLUMN dedupe_fingerprint TEXT,
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        """
    )
    op.execute(
        "ALTER TABLE pois ADD CONSTRAINT pois_canonical_id_fkey "
        "FOREIGN KEY (canonical_id) REFERENCES pois(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE pois ADD CONSTRAINT pois_embedding_dimension_check "
        "CHECK (embedding IS NULL OR cardinality(embedding) = 64)"
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id::text, name, description, category, address,
                   ST_Y(location::geometry) AS latitude,
                   ST_X(location::geometry) AS longitude
            FROM pois
            """
        )
    ).mappings()
    payloads = []
    for row in rows:
        cells = h3_cells(row["latitude"], row["longitude"])
        payloads.append(
            {
                "id": row["id"],
                "normalized_name": normalize_text(row["name"]),
                "normalized_address": normalize_text(row["address"]),
                "district": _district(row["address"]),
                "h3_r7": cells["r7"],
                "h3_r8": cells["r8"],
                "h3_r9": cells["r9"],
                "tags_json": json.dumps([row["category"]]),
                "embedding_json": json.dumps(
                    text_embedding((row["name"], row["description"], row["category"]))
                ),
                "embedding_model": EMBEDDING_MODEL,
                "fingerprint": dedupe_fingerprint(
                    row["name"], row["category"], row["latitude"], row["longitude"]
                ),
            }
        )
    if payloads:
        connection.execute(
            sa.text(
                """
                UPDATE pois SET
                    normalized_name = :normalized_name,
                    normalized_address = :normalized_address,
                    district = :district,
                    source_id = id::text,
                    canonical_id = id,
                    h3_r7 = :h3_r7,
                    h3_r8 = :h3_r8,
                    h3_r9 = :h3_r9,
                    tags = ARRAY(
                        SELECT jsonb_array_elements_text(CAST(:tags_json AS jsonb))
                    ),
                    embedding = ARRAY(
                        SELECT value::real
                        FROM jsonb_array_elements_text(CAST(:embedding_json AS jsonb)) AS value
                    ),
                    embedding_model = :embedding_model,
                    dedupe_fingerprint = :fingerprint,
                    updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            payloads,
        )
    op.execute("ALTER TABLE pois ALTER COLUMN normalized_name SET NOT NULL")
    op.execute("ALTER TABLE pois ALTER COLUMN source_id SET NOT NULL")

    op.execute("CREATE INDEX pois_normalized_name_trgm_idx ON pois USING GIN (normalized_name gin_trgm_ops)")
    op.execute("CREATE INDEX pois_normalized_address_trgm_idx ON pois USING GIN (normalized_address gin_trgm_ops)")
    op.execute("CREATE INDEX pois_h3_r7_idx ON pois (h3_r7)")
    op.execute("CREATE INDEX pois_h3_r8_idx ON pois (h3_r8)")
    op.execute("CREATE INDEX pois_h3_r9_idx ON pois (h3_r9)")
    op.execute("CREATE INDEX pois_tags_gin_idx ON pois USING GIN (tags)")
    op.execute("CREATE INDEX pois_amenities_gin_idx ON pois USING GIN (amenities)")
    op.execute("CREATE INDEX pois_district_idx ON pois (district)")
    op.execute("CREATE INDEX pois_canonical_id_idx ON pois (canonical_id)")
    op.execute("CREATE INDEX pois_dedupe_fingerprint_idx ON pois (dedupe_fingerprint)")
    op.execute("CREATE UNIQUE INDEX pois_source_identity_idx ON pois (source, source_id)")

    op.execute(
        """
        CREATE FUNCTION set_pois_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER pois_set_updated_at BEFORE UPDATE ON pois "
        "FOR EACH ROW EXECUTE FUNCTION set_pois_updated_at()"
    )

    op.execute(
        """
        CREATE TABLE poi_source_records (
            id BIGSERIAL PRIMARY KEY,
            canonical_poi_id UUID NOT NULL REFERENCES pois(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (source, source_type, source_id)
        )
        """
    )
    op.execute("CREATE INDEX poi_source_records_canonical_idx ON poi_source_records (canonical_poi_id)")
    op.execute(
        """
        INSERT INTO poi_source_records (
            canonical_poi_id, source, source_type, source_id, raw_payload
        )
        SELECT id, source, 'seed', source_id,
               jsonb_build_object('name', name, 'address', address)
        FROM pois
        """
    )

    op.execute(
        """
        CREATE TABLE poi_reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            poi_id UUID NOT NULL REFERENCES pois(id) ON DELETE CASCADE,
            user_id TEXT,
            author_name TEXT,
            rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            title TEXT,
            body TEXT NOT NULL DEFAULT '',
            language VARCHAR(12) NOT NULL DEFAULT 'vi',
            source TEXT NOT NULL DEFAULT 'user',
            source_review_id TEXT,
            helpful_count INTEGER NOT NULL DEFAULT 0 CHECK (helpful_count >= 0),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX poi_reviews_poi_time_idx ON poi_reviews (poi_id, created_at DESC)")
    op.execute("CREATE INDEX poi_reviews_user_time_idx ON poi_reviews (user_id, created_at DESC)")
    op.execute(
        "CREATE UNIQUE INDEX poi_reviews_source_identity_idx "
        "ON poi_reviews (source, source_review_id) WHERE source_review_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE user_preferences (
            user_id TEXT PRIMARY KEY,
            preferred_categories TEXT[] NOT NULL DEFAULT '{}'::text[],
            price_levels SMALLINT[] NOT NULL DEFAULT '{}'::smallint[],
            required_amenities JSONB NOT NULL DEFAULT '{}'::jsonb,
            preferred_tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            excluded_tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            home_location GEOGRAPHY(POINT, 4326),
            max_distance_meters INTEGER NOT NULL DEFAULT 5000
                CHECK (max_distance_meters BETWEEN 100 AND 100000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (price_levels <@ ARRAY[0,1,2,3,4]::smallint[])
        )
        """
    )
    op.execute(
        "CREATE INDEX user_preferences_home_gist_idx ON user_preferences "
        "USING GIST (home_location) WHERE home_location IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE geofence_subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id TEXT,
            session_id UUID,
            center GEOGRAPHY(POINT, 4326) NOT NULL,
            radius_meters INTEGER NOT NULL CHECK (radius_meters BETWEEN 50 AND 50000),
            category_filters TEXT[] NOT NULL DEFAULT '{}'::text[],
            tag_filters TEXT[] NOT NULL DEFAULT '{}'::text[],
            minimum_rating NUMERIC(2,1) CHECK (minimum_rating BETWEEN 0 AND 5),
            notify_only_when_open BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (user_id IS NOT NULL OR session_id IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX geofence_subscriptions_center_gist_idx ON geofence_subscriptions USING GIST (center)")
    op.execute(
        "CREATE INDEX geofence_subscriptions_active_idx ON geofence_subscriptions "
        "(is_active, expires_at) WHERE is_active"
    )

    op.execute(
        """
        CREATE TABLE poi_import_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            bbox DOUBLE PRECISION[] NOT NULL,
            requested_limit INTEGER NOT NULL CHECK (requested_limit > 0),
            fetched_count INTEGER NOT NULL DEFAULT 0,
            normalized_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            deduplicated_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX poi_import_runs_source_time_idx ON poi_import_runs (source, started_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS poi_import_runs")
    op.execute("DROP TABLE IF EXISTS geofence_subscriptions")
    op.execute("DROP TABLE IF EXISTS user_preferences")
    op.execute("DROP TABLE IF EXISTS poi_reviews")
    op.execute("DROP TABLE IF EXISTS poi_source_records")
    op.execute("DROP TRIGGER IF EXISTS pois_set_updated_at ON pois")
    op.execute("DROP FUNCTION IF EXISTS set_pois_updated_at()")
    op.execute("ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_embedding_dimension_check")
    op.execute("ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_canonical_id_fkey")
    op.execute(
        """
        ALTER TABLE pois
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS dedupe_fingerprint,
            DROP COLUMN IF EXISTS embedding_model,
            DROP COLUMN IF EXISTS embedding,
            DROP COLUMN IF EXISTS h3_r9,
            DROP COLUMN IF EXISTS h3_r8,
            DROP COLUMN IF EXISTS h3_r7,
            DROP COLUMN IF EXISTS canonical_id,
            DROP COLUMN IF EXISTS source_id,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS country_code,
            DROP COLUMN IF EXISTS city,
            DROP COLUMN IF EXISTS district,
            DROP COLUMN IF EXISTS brand,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS amenities,
            DROP COLUMN IF EXISTS price_level,
            DROP COLUMN IF EXISTS open_now_updated_at,
            DROP COLUMN IF EXISTS open_now,
            DROP COLUMN IF EXISTS timezone,
            DROP COLUMN IF EXISTS opening_hours,
            DROP COLUMN IF EXISTS normalized_address,
            DROP COLUMN IF EXISTS normalized_name
        """
    )
