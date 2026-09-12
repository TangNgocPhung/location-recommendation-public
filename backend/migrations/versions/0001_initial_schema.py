"""Create PostGIS POI, ingestion, session and gazetteer schema.

Revision ID: 0001_initial_schema
Revises: None
"""

from alembic import op


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS lets an existing volume created by the former init SQL be
    # adopted safely; Alembic records the version after the statements succeed.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pois (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            category_label TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            location GEOGRAPHY(POINT, 4326) NOT NULL,
            rating NUMERIC(2, 1) NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
            review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
            popularity_score DOUBLE PRECISION NOT NULL DEFAULT 0
                CHECK (popularity_score BETWEEN 0 AND 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS pois_location_gist_idx ON pois USING GIST (location)")
    op.execute("CREATE INDEX IF NOT EXISTS pois_category_idx ON pois (category)")
    op.execute("CREATE INDEX IF NOT EXISTS pois_name_trgm_idx ON pois USING GIN (name gin_trgm_ops)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS pois_description_trgm_idx "
        "ON pois USING GIN (description gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS pois_category_label_trgm_idx "
        "ON pois USING GIN (category_label gin_trgm_ops)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS geo_aliases (
            id BIGSERIAL PRIMARY KEY,
            alias TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            location GEOGRAPHY(POINT, 4326) NOT NULL,
            priority SMALLINT NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (alias, canonical_name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS geo_aliases_alias_trgm_idx "
        "ON geo_aliases USING GIN (alias gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS geo_aliases_location_gist_idx "
        "ON geo_aliases USING GIST (location)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_events (
            id UUID PRIMARY KEY,
            event_type TEXT NOT NULL CHECK (
                event_type IN (
                    'search', 'location_ping', 'poi_impression', 'poi_click',
                    'navigation_start', 'review'
                )
            ),
            session_id UUID NOT NULL,
            user_id TEXT,
            occurred_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            location GEOGRAPHY(POINT, 4326),
            accuracy_meters DOUBLE PRECISION
                CHECK (accuracy_meters IS NULL OR accuracy_meters >= 0),
            poi_id TEXT,
            query_text TEXT,
            rating SMALLINT CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
            dwell_ms INTEGER CHECK (dwell_ms IS NULL OR dwell_ms >= 0),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            processing_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (processing_status IN ('pending', 'queued', 'processed', 'failed')),
            processed_at TIMESTAMPTZ,
            failure_reason TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ingestion_events_session_time_idx "
        "ON ingestion_events (session_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ingestion_events_status_received_idx "
        "ON ingestion_events (processing_status, received_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ingestion_events_type_time_idx "
        "ON ingestion_events (event_type, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ingestion_events_location_gist_idx "
        "ON ingestion_events USING GIST (location) WHERE location IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS search_sessions (
            session_id UUID PRIMARY KEY,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_location GEOGRAPHY(POINT, 4326),
            last_query TEXT,
            event_count INTEGER NOT NULL DEFAULT 0,
            search_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_sessions")
    op.execute("DROP TABLE IF EXISTS ingestion_events")
    op.execute("DROP TABLE IF EXISTS geo_aliases")
    op.execute("DROP TABLE IF EXISTS pois")
    # Extensions are deliberately retained because other schemas may use them.
