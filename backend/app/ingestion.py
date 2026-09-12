import json
from typing import Any

import psycopg
import redis
from psycopg.rows import dict_row

from .config import settings
from .models import ClientEvent


DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
EVENT_STREAM = settings.event_stream


def _event_payload(event: ClientEvent) -> dict[str, str]:
    payload = event.model_dump(mode="json")
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "session_id": str(event.session_id),
        "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


def persist_events(events: list[ClientEvent]) -> list[str]:
    query = """
        INSERT INTO ingestion_events (
            id, event_type, session_id, user_id, occurred_at, location,
            accuracy_meters, poi_id, query_text, rating, dwell_ms, metadata
        ) VALUES (
            %(id)s, %(event_type)s, %(session_id)s, %(user_id)s, %(occurred_at)s,
            CASE WHEN CAST(%(latitude)s AS float8) IS NULL THEN NULL ELSE
                ST_SetSRID(
                    ST_Point(CAST(%(longitude)s AS float8), CAST(%(latitude)s AS float8)),
                    4326
                )::geography END,
            %(accuracy_meters)s, %(poi_id)s, %(query_text)s, %(rating)s,
            %(dwell_ms)s, %(metadata)s::jsonb
        )
        ON CONFLICT (id) DO NOTHING
        RETURNING id::text
    """
    accepted: list[str] = []
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            for event in events:
                location = event.location
                cursor.execute(
                    query,
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "session_id": event.session_id,
                        "user_id": event.user_id,
                        "occurred_at": event.occurred_at,
                        "latitude": location.latitude if location else None,
                        "longitude": location.longitude if location else None,
                        "accuracy_meters": location.accuracy_meters if location else None,
                        "poi_id": event.poi_id,
                        "query_text": event.query,
                        "rating": event.rating,
                        "dwell_ms": event.dwell_ms,
                        "metadata": json.dumps(event.metadata, ensure_ascii=False),
                    },
                )
                inserted = cursor.fetchone()
                if inserted:
                    accepted.append(inserted[0])
    return accepted


def publish_events(events: list[ClientEvent]) -> tuple[int, bool]:
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1.5)
        pipeline = client.pipeline(transaction=False)
        for event in events:
            pipeline.xadd(EVENT_STREAM, _event_payload(event), maxlen=100_000, approximate=True)
        pipeline.execute()
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE ingestion_events SET processing_status = 'queued' WHERE id = ANY(%s)",
                    ([event.id for event in events],),
                )
        return len(events), True
    except (redis.RedisError, OSError):
        return 0, False


def ingestion_status(session_id: str | None = None) -> dict[str, Any]:
    where = "WHERE session_id = %(session_id)s" if session_id else ""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE processing_status = 'pending')::int AS pending,
                    COUNT(*) FILTER (WHERE processing_status = 'queued')::int AS queued,
                    COUNT(*) FILTER (WHERE processing_status = 'processed')::int AS processed,
                    COUNT(*) FILTER (WHERE processing_status = 'failed')::int AS failed,
                    -- Số ping bị bộ lọc GPS từ chối (fake GPS, teleport, ngoài
                    -- vùng phục vụ) và số ping độ chính xác kém. Con số này đưa
                    -- thẳng vào báo cáo được.
                    COUNT(*) FILTER (WHERE metadata->>'geo_accepted' = 'false')::int AS geo_rejected,
                    COUNT(*) FILTER (WHERE metadata->>'geo_low_quality' = 'true')::int AS geo_low_quality,
                    MAX(received_at) AS last_received_at
                FROM ingestion_events
                {where}
                """,
                {"session_id": session_id} if session_id else {},
            )
            counts = cursor.fetchone()
    redis_ok = False
    stream_length = 0
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1)
        stream_length = client.xlen(EVENT_STREAM)
        redis_ok = True
    except (redis.RedisError, OSError):
        pass
    return {
        "storage": "postgresql",
        "stream": "redis-streams",
        "redisConnected": redis_ok,
        "streamLength": stream_length,
        **counts,
    }
