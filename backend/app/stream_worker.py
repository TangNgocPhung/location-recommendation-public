import json
import logging
import time
from datetime import datetime, timezone

import psycopg
import redis

from . import geo_cache, geo_quality, geofence
from .config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("nearby-stream-worker")

DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
EVENT_STREAM = settings.event_stream
CONSUMER_GROUP = settings.consumer_group
CONSUMER_NAME = settings.consumer_name
MAX_DELIVERY_ATTEMPTS = settings.max_delivery_attempts
RECLAIM_IDLE_MS = settings.reclaim_idle_ms
# Dead-letter stream: sự kiện hỏng vĩnh viễn được chuyển sang đây rồi XACK, thay
# vì nằm lại Pending Entries List mãi mãi.
DLQ_STREAM = f"{EVENT_STREAM}:dlq"


def ensure_consumer_group(client: redis.Redis) -> None:
    try:
        client.xgroup_create(EVENT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


def requeue_pending(client: redis.Redis) -> int:
    query = """
        SELECT id::text, event_type, session_id::text, occurred_at, poi_id,
               query_text, rating, dwell_ms,
               CASE WHEN location IS NULL THEN NULL ELSE ST_Y(location::geometry) END AS latitude,
               CASE WHEN location IS NULL THEN NULL ELSE ST_X(location::geometry) END AS longitude,
               accuracy_meters, metadata
        FROM ingestion_events
        WHERE processing_status = 'pending'
        ORDER BY received_at
        LIMIT 100
        FOR UPDATE SKIP LOCKED
    """
    queued = 0
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            for row in cursor.fetchall():
                payload = {
                    "id": row[0],
                    "event_type": row[1],
                    "session_id": row[2],
                    "occurred_at": row[3].isoformat(),
                    "poi_id": row[4],
                    "query": row[5],
                    "rating": row[6],
                    "dwell_ms": row[7],
                    "location": (
                        {
                            "latitude": row[8],
                            "longitude": row[9],
                            "accuracy_meters": row[10],
                        }
                        if row[8] is not None
                        else None
                    ),
                    "metadata": row[11],
                }
                client.xadd(
                    EVENT_STREAM,
                    {
                        "event_id": row[0],
                        "event_type": row[1],
                        "session_id": row[2],
                        "payload": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                    maxlen=100_000,
                    approximate=True,
                )
                cursor.execute(
                    "UPDATE ingestion_events SET processing_status = 'queued' WHERE id = %s",
                    (row[0],),
                )
                queued += 1
    return queued


def _previous_location(session_id: str) -> tuple[float, float, datetime] | None:
    """Vị trí và thời điểm của ping trước cùng phiên, để tính tốc độ ngầm."""
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ST_Y(last_location::geometry), ST_X(last_location::geometry),
                           last_seen_at
                    FROM search_sessions
                    WHERE session_id = %s AND last_location IS NOT NULL
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
    except psycopg.Error:
        return None
    return (row[0], row[1], row[2]) if row else None


def _evaluate_location(
    session_id: str, location: dict | None, occurred_at_raw: str | None
) -> geo_quality.GeoVerdict:
    if not location:
        return geo_quality.GeoVerdict(accepted=True, low_quality=False)
    occurred_at = None
    if occurred_at_raw:
        try:
            occurred_at = datetime.fromisoformat(occurred_at_raw)
        except ValueError:
            occurred_at = None
    return geo_quality.evaluate(
        location.get("latitude"),
        location.get("longitude"),
        location.get("accuracy_meters"),
        previous=_previous_location(session_id) if occurred_at else None,
        occurred_at=occurred_at,
    )


def process_event(client: redis.Redis, fields: dict[str, str]) -> None:
    event = json.loads(fields["payload"])
    event_id = event["id"]
    event_type = event["event_type"]
    session_id = event["session_id"]
    poi_id = event.get("poi_id")
    query_text = event.get("query")
    location = event.get("location")

    # Chấm chất lượng toạ độ TRƯỚC khi cho vào bất kỳ tín hiệu hạ nguồn nào.
    # Toạ độ bị loại vẫn được lưu trong ingestion_events kèm lý do (để còn đếm
    # và điều tra), nhưng không được GEOADD, không cập nhật last_location và
    # không tính vào trending.
    verdict = _evaluate_location(session_id, location, event.get("occurred_at"))
    if not verdict.accepted:
        logger.warning(
            "Loại toạ độ của phiên %s: %s (%s km/h)",
            session_id,
            verdict.reason,
            verdict.speed_kmh,
        )

    pipeline = client.pipeline(transaction=False)
    pipeline.hset(
        f"nearby:session:{session_id}",
        mapping={
            "last_event": event_type,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    pipeline.expire(f"nearby:session:{session_id}", 86_400)
    # Trending đi qua geo_cache: khung giờ có TTL (bộ đếm biết quên) và thêm một
    # bộ đếm theo ô H3 của người dùng lúc tương tác (trending có địa điểm).
    #
    # Toạ độ chỉ được dùng khi đã qua bộ chấm chất lượng. Một ping GPS giả mà
    # lọt vào đây sẽ bơm điểm trending cho một khu vực người đó chưa từng tới —
    # cùng lý do khiến toạ độ bị loại không được GEOADD.
    trusted = location if (location and verdict.accepted) else None
    if poi_id and event_type in {"poi_click", "navigation_start", "review"}:
        weight = {"poi_click": 1, "navigation_start": 3, "review": 5}[event_type]
        geo_cache.record_poi_interaction(
            pipeline,
            poi_id,
            weight,
            latitude=trusted.get("latitude") if trusted else None,
            longitude=trusted.get("longitude") if trusted else None,
        )
    if query_text and event_type == "search":
        geo_cache.record_query(pipeline, query_text.strip().lower())
    if trusted:
        pipeline.geoadd(
            geo_cache.ACTIVE_LOCATIONS_KEY,
            (trusted["longitude"], trusted["latitude"], session_id),
        )
    pipeline.execute()

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO search_sessions (
                    session_id, last_seen_at, last_location, last_query, event_count, search_count
                ) VALUES (
                    %(session_id)s, NOW(),
                    CASE WHEN CAST(%(latitude)s AS float8) IS NULL THEN NULL ELSE
                        ST_SetSRID(
                            ST_Point(CAST(%(longitude)s AS float8), CAST(%(latitude)s AS float8)),
                            4326
                        )::geography END,
                    %(query_text)s, 1, %(search_increment)s
                )
                ON CONFLICT (session_id) DO UPDATE SET
                    last_seen_at = NOW(),
                    last_location = COALESCE(EXCLUDED.last_location, search_sessions.last_location),
                    last_query = COALESCE(EXCLUDED.last_query, search_sessions.last_query),
                    event_count = search_sessions.event_count + 1,
                    search_count = search_sessions.search_count + EXCLUDED.search_count
                """,
                {
                    "session_id": session_id,
                    "latitude": location.get("latitude") if location and verdict.accepted else None,
                    "longitude": location.get("longitude") if location and verdict.accepted else None,
                    "query_text": query_text,
                    "search_increment": 1 if event_type == "search" else 0,
                },
            )
            cursor.execute(
                """
                UPDATE ingestion_events
                SET processing_status = 'processed',
                    processed_at = NOW(),
                    failure_reason = NULL,
                    metadata = metadata || CAST(%s AS jsonb)
                WHERE id = %s
                """,
                (json.dumps(verdict.as_metadata()), event_id),
            )

    # Proximity Notification Service: chỉ ping ĐÃ qua bộ chấm chất lượng mới
    # được kích hoạt vùng nhắc. Một toạ độ giả làm người dùng nhận thông báo
    # "bạn đang ở gần X" trong khi họ đang ngồi ở nhà — hỏng niềm tin nhanh hơn
    # bất kỳ lỗi xếp hạng nào.
    if event_type == "location_ping" and trusted:
        notifications = geofence.evaluate_ping(
            session_id,
            float(trusted["latitude"]),
            float(trusted["longitude"]),
            event_id=event_id,
        )
        if notifications:
            geofence.publish(client, notifications)
            logger.info(
                "Phiên %s kích hoạt %d vùng nhắc", session_id, len(notifications)
            )


def mark_failed(event_id: str | None, reason: str) -> None:
    """Đánh dấu sự kiện là hỏng vĩnh viễn kèm lý do.

    Cột `failure_reason` có sẵn từ 0001 nhưng trước đây không bao giờ được ghi:
    nhánh except cũ chỉ log rồi bỏ qua, nên sự kiện giữ nguyên trạng thái
    'queued' và `requeue_pending` (chỉ quét 'pending') không bao giờ lấy lại.
    """
    if not event_id:
        return
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingestion_events
                    SET processing_status = 'failed',
                        processed_at = NOW(),
                        failure_reason = %s
                    WHERE id = %s
                    """,
                    (reason[:500], event_id),
                )
    except psycopg.Error:
        logger.exception("Không ghi được failure_reason cho %s", event_id)


def send_to_dlq(client: redis.Redis, message_id: str, fields: dict, reason: str) -> None:
    """Chuyển sang dead-letter stream rồi XACK để giải phóng consumer group."""
    payload = dict(fields)
    payload["dlq_reason"] = reason[:500]
    payload["dlq_at"] = datetime.now(timezone.utc).isoformat()
    payload["dlq_source_id"] = message_id
    try:
        client.xadd(DLQ_STREAM, payload, maxlen=10_000, approximate=True)
    except redis.RedisError:
        logger.exception("Không ghi được vào DLQ, giữ nguyên message %s", message_id)
        return
    client.xack(EVENT_STREAM, CONSUMER_GROUP, message_id)
    mark_failed(fields.get("event_id") or fields.get("id"), reason)
    logger.error("Sự kiện %s vào DLQ sau %d lần thử: %s", message_id, MAX_DELIVERY_ATTEMPTS, reason)


def _delivery_count(client: redis.Redis, message_id: str) -> int:
    """Số lần message đã được giao. Redis đếm sẵn trong PEL, không cần tự đếm."""
    try:
        pending = client.xpending_range(
            EVENT_STREAM, CONSUMER_GROUP, min=message_id, max=message_id, count=1
        )
    except redis.RedisError:
        return 1
    return int(pending[0]["times_delivered"]) if pending else 1


def handle_message(client: redis.Redis, message_id: str, fields: dict) -> bool:
    """Xử lý một message; trả True nếu đã XACK (thành công hoặc vào DLQ)."""
    try:
        process_event(client, fields)
        client.xack(EVENT_STREAM, CONSUMER_GROUP, message_id)
        return True
    except Exception as error:  # noqa: BLE001 - phải bắt hết để không chặn cả group
        attempts = _delivery_count(client, message_id)
        logger.warning(
            "Sự kiện %s lỗi lần %d/%d: %s",
            fields.get("event_id"),
            attempts,
            MAX_DELIVERY_ATTEMPTS,
            error,
        )
        if attempts >= MAX_DELIVERY_ATTEMPTS:
            send_to_dlq(client, message_id, fields, f"{type(error).__name__}: {error}")
            return True
        # Chưa hết lượt: KHÔNG xack, để message ở lại PEL cho lần reclaim sau.
        return False


def reclaim_stale(client: redis.Redis) -> int:
    """Giành lại message kẹt trong PEL của consumer đã chết (XAUTOCLAIM).

    Không có bước này, một worker bị kill giữa chừng sẽ mang theo toàn bộ message
    nó đang giữ: chúng nằm trong PEL của một consumer không bao giờ quay lại, và
    không worker nào khác đọc được vì XREADGROUP ">" chỉ trả message MỚI.
    """
    handled = 0
    try:
        _, entries, _ = client.xautoclaim(
            EVENT_STREAM,
            CONSUMER_GROUP,
            CONSUMER_NAME,
            min_idle_time=RECLAIM_IDLE_MS,
            count=25,
        )
    except redis.ResponseError:
        return 0
    for message_id, fields in entries:
        if not fields:
            # Message gốc đã bị xoá khỏi stream; XACK để dọn PEL.
            client.xack(EVENT_STREAM, CONSUMER_GROUP, message_id)
            continue
        if handle_message(client, message_id, fields):
            handled += 1
    if handled:
        logger.info("Đã giành lại %d message kẹt trong PEL", handled)
    return handled


def run() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        try:
            client.ping()
            ensure_consumer_group(client)
            break
        except (redis.RedisError, OSError):
            logger.info("Waiting for Redis")
            time.sleep(2)

    logger.info("Stream worker ready: stream=%s group=%s", EVENT_STREAM, CONSUMER_GROUP)
    last_requeue = 0.0
    while True:
        try:
            if time.monotonic() - last_requeue > 10:
                requeue_pending(client)
                # Giành lại message kẹt trong PEL cùng nhịp với requeue: cả hai
                # đều là đường cứu dữ liệu đã nhận nhưng chưa xử lý xong.
                reclaim_stale(client)
                last_requeue = time.monotonic()
            messages = client.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {EVENT_STREAM: ">"},
                count=25,
                block=2_000,
            )
            for _, entries in messages:
                for message_id, fields in entries:
                    handle_message(client, message_id, fields)
        except (redis.RedisError, psycopg.Error, OSError):
            logger.exception("Worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    run()
