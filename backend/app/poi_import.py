"""OpenStreetMap ingestion with normalization, lineage and spatial deduplication."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .poi_features import normalize_district, normalize_osm_element


OSM_FILTERS = {
    "amenity": (
        "cafe|restaurant|fast_food|bar|pub|hospital|clinic|pharmacy|school|"
        "university|bank|atm|marketplace|cinema|theatre|library"
    ),
    "tourism": "museum|attraction|viewpoint|hotel|gallery",
    "leisure": "park|garden|fitness_centre|sports_centre|playground",
    "shop": "supermarket|mall|convenience|books|bakery|clothes|electronics",
}


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = tuple(float(part.strip()) for part in value.split(","))
    if len(parts) != 4:
        raise ValueError("bbox must contain south,west,north,east")
    south, west, north, east = parts
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("bbox coordinates are invalid")
    return south, west, north, east


def build_overpass_query(bbox: tuple[float, float, float, float]) -> str:
    bounds = ",".join(str(value) for value in bbox)
    selectors = "\n".join(
        f'  nwr["name"]["{key}"~"^({values})$"]({bounds});'
        for key, values in OSM_FILTERS.items()
    )
    return f"[out:json][timeout:180];\n(\n{selectors}\n);\nout center tags;"


def fetch_overpass_elements(
    url: str,
    bbox: tuple[float, float, float, float],
    timeout_seconds: int = 210,
) -> list[dict[str, Any]]:
    body = urllib.parse.urlencode({"data": build_overpass_query(bbox)}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "nearby-location-recommendation/0.3 (educational project)",
        },
        method="POST",
    )
    retryable_statuses = {429, 502, 503, 504}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
            return payload.get("elements", [])
        except urllib.error.HTTPError as error:
            if error.code not in retryable_statuses or attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2**attempt)
    return []


def _normalized_elements(
    elements: Iterable[dict[str, Any]],
    max_pois: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for element in elements:
        poi = normalize_osm_element(element)
        if not poi or poi["source_id"] in source_ids:
            continue
        source_ids.add(poi["source_id"])
        normalized.append(poi)
        if len(normalized) >= max_pois:
            break
    return normalized


def _find_existing(cursor: psycopg.Cursor[Any], poi: dict[str, Any]) -> tuple[str | None, bool]:
    cursor.execute(
        """
        SELECT canonical_poi_id::text
        FROM poi_source_records
        WHERE source = %(source)s AND source_type = 'poi' AND source_id = %(source_id)s
        """,
        poi,
    )
    exact = cursor.fetchone()
    if exact:
        return exact["canonical_poi_id"], False

    cursor.execute(
        """
        SELECT id::text
        FROM pois
        WHERE category = %(category)s
          AND ST_DWithin(
              location,
              ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography,
              75
          )
          AND (
              normalized_name = %(normalized_name)s
              OR similarity(normalized_name, %(normalized_name)s) >= 0.84
          )
        ORDER BY
            (normalized_name = %(normalized_name)s) DESC,
            similarity(normalized_name, %(normalized_name)s) DESC,
            ST_Distance(
                location,
                ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography
            ) ASC
        LIMIT 1
        """,
        poi,
    )
    spatial_match = cursor.fetchone()
    return (spatial_match["id"], True) if spatial_match else (None, False)


def _merge_poi(cursor: psycopg.Cursor[Any], poi_id: str, poi: dict[str, Any]) -> None:
    params = _db_params(poi) | {"poi_id": poi_id}
    cursor.execute(
        """
        UPDATE pois SET
            description = COALESCE(NULLIF(%(description)s, ''), description),
            address = COALESCE(NULLIF(%(address)s, ''), address),
            normalized_address = COALESCE(NULLIF(%(normalized_address)s, ''), normalized_address),
            opening_hours = CASE
                WHEN %(opening_hours)s::jsonb = '{}'::jsonb THEN opening_hours
                ELSE %(opening_hours)s::jsonb
            END,
            timezone = %(timezone)s,
            open_now = %(open_now)s,
            open_now_updated_at = NOW(),
            amenities = amenities || %(amenities)s::jsonb,
            tags = ARRAY(
                SELECT DISTINCT value FROM unnest(tags || %(tags)s::text[]) AS value
                ORDER BY value
            ),
            brand = COALESCE(NULLIF(%(brand)s, ''), brand),
            district = CASE
                WHEN pois.source = 'openstreetmap' THEN %(district)s
                ELSE COALESCE(NULLIF(%(district)s, ''), district)
            END,
            city = COALESCE(NULLIF(%(city)s, ''), city),
            country_code = %(country_code)s,
            embedding = %(embedding)s::real[],
            embedding_model = %(embedding_model)s,
            dedupe_fingerprint = COALESCE(dedupe_fingerprint, %(dedupe_fingerprint)s)
        WHERE id = %(poi_id)s::uuid
        """,
        params,
    )


def _insert_poi(cursor: psycopg.Cursor[Any], poi: dict[str, Any]) -> str:
    cursor.execute(
        """
        INSERT INTO pois (
            id, name, normalized_name, description, category, category_label,
            address, normalized_address, location, opening_hours, timezone,
            open_now, open_now_updated_at, price_level, amenities, tags, brand,
            district, city, country_code, source, source_id, h3_r7, h3_r8, h3_r9,
            embedding, embedding_model, dedupe_fingerprint
        ) VALUES (
            gen_random_uuid(), %(name)s, %(normalized_name)s, %(description)s,
            %(category)s, %(category_label)s, %(address)s, %(normalized_address)s,
            ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography,
            %(opening_hours)s::jsonb, %(timezone)s, %(open_now)s, NOW(),
            %(price_level)s, %(amenities)s::jsonb, %(tags)s::text[], %(brand)s,
            %(district)s, %(city)s, %(country_code)s, %(source)s, %(source_id)s,
            %(h3_r7)s, %(h3_r8)s, %(h3_r9)s, %(embedding)s::real[],
            %(embedding_model)s, %(dedupe_fingerprint)s
        ) RETURNING id::text
        """,
        _db_params(poi),
    )
    poi_id = cursor.fetchone()["id"]
    cursor.execute("UPDATE pois SET canonical_id = id WHERE id = %s::uuid", (poi_id,))
    return poi_id


def _db_params(poi: dict[str, Any]) -> dict[str, Any]:
    return poi | {
        "opening_hours": Jsonb(poi["opening_hours"]),
        "amenities": Jsonb(poi["amenities"]),
    }


def _upsert_lineage(cursor: psycopg.Cursor[Any], poi_id: str, poi: dict[str, Any]) -> None:
    cursor.execute(
        """
        INSERT INTO poi_source_records (
            canonical_poi_id, source, source_type, source_id, raw_payload
        ) VALUES (
            %(poi_id)s::uuid, %(source)s, 'poi', %(source_id)s, %(raw_payload)s::jsonb
        )
        ON CONFLICT (source, source_type, source_id) DO UPDATE SET
            canonical_poi_id = EXCLUDED.canonical_poi_id,
            raw_payload = EXCLUDED.raw_payload,
            last_seen_at = NOW()
        """,
        poi | {"poi_id": poi_id, "raw_payload": Jsonb(poi["raw_payload"])},
    )


def import_osm_elements(
    database_url: str,
    elements: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    max_pois: int,
) -> dict[str, Any]:
    if max_pois < 1:
        raise ValueError("max_pois must be positive")
    normalized = _normalized_elements(elements, max_pois)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO poi_import_runs (source, status, bbox, requested_limit)
                VALUES ('openstreetmap', 'running', %s::double precision[], %s)
                RETURNING id::text
                """,
                (list(bbox), max_pois),
            )
            run_id = cursor.fetchone()["id"]

    stats: dict[str, Any] = {
        "runId": run_id,
        "fetched": len(elements),
        "normalized": len(normalized),
        "inserted": 0,
        "updated": 0,
        "deduplicated": 0,
    }
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                for poi in normalized:
                    poi_id, deduplicated = _find_existing(cursor, poi)
                    if poi_id:
                        _merge_poi(cursor, poi_id, poi)
                        stats["updated"] += 1
                        stats["deduplicated"] += int(deduplicated)
                    else:
                        poi_id = _insert_poi(cursor, poi)
                        stats["inserted"] += 1
                    _upsert_lineage(cursor, poi_id, poi)
                cursor.execute(
                    """
                    UPDATE poi_import_runs SET
                        status = 'completed', fetched_count = %(fetched)s,
                        normalized_count = %(normalized)s, inserted_count = %(inserted)s,
                        updated_count = %(updated)s, deduplicated_count = %(deduplicated)s,
                        finished_at = clock_timestamp()
                    WHERE id = %(runId)s::uuid
                    """,
                    stats,
                )
    except Exception as error:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE poi_import_runs SET status = 'failed', error_message = %s,
                        finished_at = clock_timestamp() WHERE id = %s::uuid
                    """,
                    (str(error)[:2000], run_id),
                )
        raise
    return stats


def refresh_osm_districts(database_url: str) -> int:
    """Recompute canonical district names from already persisted source payloads."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (canonical_poi_id)
                       canonical_poi_id::text AS poi_id, raw_payload
                FROM poi_source_records
                WHERE source = 'openstreetmap' AND source_type = 'poi'
                ORDER BY canonical_poi_id, last_seen_at DESC
                """
            )
            updates = []
            for row in cursor.fetchall():
                tags = (row["raw_payload"] or {}).get("tags") or {}
                raw_district = (
                    tags.get("addr:district")
                    or tags.get("addr:city_district")
                    or tags.get("addr:suburb")
                )
                updates.append(
                    {"poi_id": row["poi_id"], "district": normalize_district(raw_district)}
                )
            if updates:
                cursor.executemany(
                    """
                    UPDATE pois SET district = %(district)s
                    WHERE id = %(poi_id)s::uuid AND source = 'openstreetmap'
                    """,
                    updates,
                )
    return len(updates)


def data_status(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE source = 'openstreetmap')::int AS osm,
                    COUNT(DISTINCT district)::int AS districts,
                    COUNT(*) FILTER (WHERE h3_r9 IS NOT NULL)::int AS with_h3,
                    COUNT(*) FILTER (WHERE embedding IS NOT NULL)::int AS with_embedding,
                    COUNT(*) FILTER (WHERE opening_hours <> '{}'::jsonb)::int AS with_opening_hours
                FROM pois
                """
            )
            summary = dict(cursor.fetchone())
            cursor.execute(
                """
                SELECT id::text AS id, status, fetched_count AS "fetchedCount",
                       normalized_count AS "normalizedCount", inserted_count AS "insertedCount",
                       updated_count AS "updatedCount", deduplicated_count AS "deduplicatedCount",
                       started_at AS "startedAt", finished_at AS "finishedAt"
                FROM poi_import_runs ORDER BY started_at DESC LIMIT 1
                """
            )
            latest = cursor.fetchone()
    return {"pois": summary, "latestImport": dict(latest) if latest else None}
