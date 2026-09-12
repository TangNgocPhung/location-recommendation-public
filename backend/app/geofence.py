"""Proximity Notification Service (lộ trình B13b).

Ô này trong sơ đồ tầng 4 đang trống ở tầng chạy, dù schema đã được thiết kế rất
kỹ từ migration 0003: ``geofence_subscriptions`` có center GEOGRAPHY,
radius_meters, category_filters, tag_filters, minimum_rating,
notify_only_when_open, expires_at, GIST index và partial index — nhưng chưa một
dòng code ngoài migration nào đụng tới.

Luồng:

1. Người dùng bấm "Nhắc tôi khi tới gần" trên một POI → ``subscribe``.
2. ``stream_worker`` gặp ``location_ping`` → ``evaluate_ping`` chạy ST_DWithin
   trên đúng GIST index đã tạo.
3. Lượt kích hoạt hợp lệ được ghi vào ``geofence_hits`` rồi XADD vào stream
   ``nearby:notifications``.
4. API ``/api/v1/notifications/stream`` (SSE) đẩy xuống trình duyệt.

**Chống báo trùng là phần quan trọng nhất của module này.** Người dùng đứng yên
trong vùng, mỗi 20 giây một ping, sẽ nhận mỗi 20 giây một thông báo nếu không
có gì chặn. Nên mỗi đăng ký có ``cooldown_minutes`` (mặc định 30) và câu lệnh
lọc trực tiếp trong SQL bằng ``NOT EXISTS`` trên ``geofence_hits`` — kiểm tra ở
Python thì hai ping xử lý gần nhau vẫn lọt cả hai.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import psycopg
import redis
from psycopg.rows import dict_row

from .config import settings

logger = logging.getLogger("nearby-geofence")

DATABASE_URL = settings.database_url
NOTIFICATION_STREAM = "nearby:notifications"

# Trần số thông báo giữ lại trong stream Redis. Stream không có TTL tự nhiên;
# không cắt thì nó phình vô hạn trong một tiến trình chạy dài.
NOTIFICATION_STREAM_MAXLEN = 1_000

DEFAULT_RADIUS_METERS = 300
MIN_RADIUS_METERS = 50
MAX_RADIUS_METERS = 50_000


# --- Đăng ký ------------------------------------------------------------------


_INSERT_SQL = """
    INSERT INTO geofence_subscriptions (
        session_id, poi_id, label, center, radius_meters,
        category_filters, minimum_rating, notify_only_when_open,
        cooldown_minutes, expires_at
    )
    SELECT
        %(session_id)s, p.id, COALESCE(%(label)s, p.name), p.location, %(radius)s,
        %(categories)s::text[], %(minimum_rating)s, %(only_when_open)s,
        %(cooldown)s, %(expires_at)s
    FROM pois p
    WHERE p.id = %(poi_id)s
    ON CONFLICT (session_id, poi_id) WHERE poi_id IS NOT NULL AND session_id IS NOT NULL
    DO UPDATE SET
        radius_meters = EXCLUDED.radius_meters,
        label = EXCLUDED.label,
        minimum_rating = EXCLUDED.minimum_rating,
        notify_only_when_open = EXCLUDED.notify_only_when_open,
        cooldown_minutes = EXCLUDED.cooldown_minutes,
        expires_at = EXCLUDED.expires_at,
        is_active = TRUE,
        updated_at = NOW()
    RETURNING id::text AS id, poi_id::text AS "poiId", label, radius_meters AS "radiusMeters",
              cooldown_minutes AS "cooldownMinutes", is_active AS "isActive",
              expires_at AS "expiresAt", created_at AS "createdAt"
"""


def subscribe(
    session_id: str,
    poi_id: str,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    label: str | None = None,
    minimum_rating: float | None = None,
    notify_only_when_open: bool = False,
    cooldown_minutes: int = 30,
    expires_at: Any = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    """Tạo (hoặc cập nhật) vùng nhắc quanh một POI.

    Tâm vùng lấy thẳng từ ``pois.location`` trong cùng một câu lệnh, không nhận
    toạ độ do client gửi lên: client gửi toạ độ thì một lỗi phía giao diện sẽ
    đặt vùng nhắc ở sai chỗ mà không ai phát hiện được.

    Trả ``None`` khi ``poi_id`` không tồn tại — câu SELECT không ra dòng nào nên
    không có gì được chèn.
    """
    radius = max(MIN_RADIUS_METERS, min(MAX_RADIUS_METERS, int(radius_meters)))
    params = {
        "session_id": session_id,
        "poi_id": poi_id,
        "label": label,
        "radius": radius,
        "categories": [],
        "minimum_rating": minimum_rating,
        "only_when_open": notify_only_when_open,
        "cooldown": cooldown_minutes,
        "expires_at": expires_at,
    }
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_INSERT_SQL, params)
            return cursor.fetchone()


_LIST_SQL = """
    SELECT g.id::text AS id, g.poi_id::text AS "poiId", g.label,
           g.radius_meters AS "radiusMeters", g.cooldown_minutes AS "cooldownMinutes",
           g.is_active AS "isActive", g.expires_at AS "expiresAt",
           g.created_at AS "createdAt",
           p.name AS "poiName", p.category, p.address,
           ST_Y(p.location::geometry) AS latitude,
           ST_X(p.location::geometry) AS longitude,
           (SELECT MAX(h.occurred_at) FROM geofence_hits h
             WHERE h.subscription_id = g.id) AS "lastHitAt"
    FROM geofence_subscriptions g
    LEFT JOIN pois p ON p.id = g.poi_id
    WHERE g.session_id = %(session_id)s
      AND g.is_active
      AND (g.expires_at IS NULL OR g.expires_at > NOW())
    ORDER BY g.created_at DESC
"""


def list_subscriptions(session_id: str, database_url: str | None = None) -> list[dict[str, Any]]:
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_LIST_SQL, {"session_id": session_id})
            return list(cursor.fetchall())


def unsubscribe(
    session_id: str, subscription_id: str, database_url: str | None = None
) -> bool:
    """Tắt một vùng nhắc. Đánh dấu ``is_active = FALSE`` chứ không xoá hàng:
    ``geofence_hits`` tham chiếu tới nó, và lịch sử đã kích hoạt là dữ liệu để
    đánh giá, không phải rác.

    Điều kiện có cả ``session_id`` — thiếu nó thì bất kỳ ai đoán được UUID là
    tắt được vùng nhắc của người khác.
    """
    with psycopg.connect(database_url or DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE geofence_subscriptions
                SET is_active = FALSE, updated_at = NOW()
                WHERE id = %(id)s AND session_id = %(session_id)s AND is_active
                """,
                {"id": subscription_id, "session_id": session_id},
            )
            return cursor.rowcount > 0


# --- Đánh giá một ping ---------------------------------------------------------


# ST_DWithin trên cột GEOGRAPHY dùng đúng geofence_subscriptions_center_gist_idx.
# NOT EXISTS là bộ chống báo trùng, và nó phải nằm TRONG câu lệnh: lọc ở Python
# thì hai ping được xử lý gần nhau vẫn lọt cả hai.
_MATCH_SQL = """
    WITH ping AS (
        SELECT ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography AS point
    )
    SELECT g.id::text AS "subscriptionId", g.poi_id::text AS "poiId",
           COALESCE(g.label, p.name) AS label, g.radius_meters AS "radiusMeters",
           p.name AS "poiName", p.category, p.address,
           ST_Y(p.location::geometry) AS latitude,
           ST_X(p.location::geometry) AS longitude,
           ST_Distance(g.center, ping.point) AS "distanceMeters"
    FROM geofence_subscriptions g
    CROSS JOIN ping
    LEFT JOIN pois p ON p.id = g.poi_id
    WHERE g.session_id = %(session_id)s
      AND g.is_active
      AND (g.expires_at IS NULL OR g.expires_at > NOW())
      AND ST_DWithin(g.center, ping.point, g.radius_meters)
      AND NOT EXISTS (
          SELECT 1 FROM geofence_hits h
          WHERE h.subscription_id = g.id
            AND h.occurred_at > NOW() - make_interval(mins => g.cooldown_minutes)
      )
    ORDER BY "distanceMeters"
"""

_RECORD_HIT_SQL = """
    INSERT INTO geofence_hits (subscription_id, session_id, event_id, poi_id, distance_meters)
    VALUES (%(subscription_id)s, %(session_id)s, %(event_id)s, %(poi_id)s, %(distance)s)
    RETURNING id::text AS id, occurred_at AS "occurredAt"
"""


def evaluate_ping(
    session_id: str,
    latitude: float,
    longitude: float,
    event_id: str | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """Tìm vùng nhắc mà ping này vừa bước vào, ghi lại, trả danh sách thông báo.

    Một kết nối, một transaction: tìm và ghi phải nằm cùng chỗ, nếu không thì
    hai ping xử lý song song cùng vượt qua bộ chống báo trùng.
    """
    notifications: list[dict[str, Any]] = []
    try:
        with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _MATCH_SQL,
                    {
                        "session_id": session_id,
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                )
                matches = list(cursor.fetchall())
                for match in matches:
                    cursor.execute(
                        _RECORD_HIT_SQL,
                        {
                            "subscription_id": match["subscriptionId"],
                            "session_id": session_id,
                            "event_id": event_id,
                            "poi_id": match["poiId"],
                            "distance": match["distanceMeters"],
                        },
                    )
                    hit = cursor.fetchone() or {}
                    notifications.append(
                        {
                            "hitId": hit.get("id"),
                            "sessionId": session_id,
                            "subscriptionId": match["subscriptionId"],
                            "poiId": match["poiId"],
                            "title": match["label"] or match["poiName"] or "Địa điểm đã lưu",
                            "category": match["category"],
                            "address": match["address"],
                            "latitude": match["latitude"],
                            "longitude": match["longitude"],
                            "distanceMeters": round(float(match["distanceMeters"]), 1),
                            "radiusMeters": match["radiusMeters"],
                        }
                    )
    except psycopg.Error as error:
        # Geofence hỏng không được làm hỏng cả việc xử lý sự kiện: ping vẫn phải
        # được ghi nhận, trending vẫn phải cập nhật.
        logger.warning("Đánh giá geofence lỗi, bỏ qua ping này: %s", error)
        return []
    return notifications


def publish(client: Any, notifications: list[dict[str, Any]]) -> int:
    """Đẩy thông báo vào stream Redis cho endpoint SSE đọc."""
    if not notifications:
        return 0
    sent = 0
    for notification in notifications:
        try:
            client.xadd(
                NOTIFICATION_STREAM,
                {"payload": json.dumps(notification, ensure_ascii=False)},
                maxlen=NOTIFICATION_STREAM_MAXLEN,
                approximate=True,
            )
            sent += 1
        except (redis.RedisError, OSError, TypeError) as error:
            logger.warning("Không đẩy được thông báo vào stream: %s", error)
    return sent


def mark_notified(hit_ids: list[str], database_url: str | None = None) -> int:
    """Đánh dấu đã gửi tới người dùng.

    Tách khỏi lúc ghi ``geofence_hits``: "đã phát hiện" và "đã báo cho người
    dùng" là hai việc khác nhau, và trộn chúng lại thì một lần mất kết nối SSE
    sẽ nuốt luôn thông báo mà không để lại dấu vết nào.
    """
    if not hit_ids:
        return 0
    try:
        with psycopg.connect(database_url or DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE geofence_hits SET notified_at = NOW() "
                    "WHERE id = ANY(%(ids)s::uuid[]) AND notified_at IS NULL",
                    {"ids": hit_ids},
                )
                return cursor.rowcount
    except psycopg.Error as error:
        logger.warning("Không đánh dấu được thông báo đã gửi: %s", error)
        return 0


def is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# --- Kênh SSE ------------------------------------------------------------------

# Trần thời gian sống của một kết nối SSE. Trình duyệt tự kết nối lại, nên cắt
# định kỳ là an toàn; không cắt thì mỗi tab bỏ quên giữ một luồng vĩnh viễn
# trong threadpool của FastAPI và server hết luồng trước khi hết bộ nhớ.
STREAM_TTL_SECONDS = 300

# Nhịp tim. Proxy và trình duyệt đóng kết nối im lặng quá lâu; một dòng comment
# SSE (bắt đầu bằng ":") giữ kết nối sống mà không phải là một sự kiện.
HEARTBEAT_SECONDS = 15

# Timeout socket PHẢI dài hơn thời gian XREAD chặn, nếu không socket hết giờ
# trước khi lệnh chặn kịp trả về và mọi kết nối SSE chết sau đúng
# `socket_timeout` giây với "Timeout reading from socket" — kèm một vòng lặp
# kết nối lại vô tận mà nhìn từ phía trình duyệt thì không khác gì server chập
# chờn. Đã dính đúng lỗi này khi chạy thật lần đầu.
STREAM_SOCKET_TIMEOUT = HEARTBEAT_SECONDS + 5


def stream_notifications(session_id: str, client: Any | None = None):
    """Sinh khung SSE cho một phiên, đọc từ stream Redis.

    Bắt đầu từ ``$`` (chỉ sự kiện MỚI): đọc từ đầu stream thì một tab vừa mở sẽ
    dội lại toàn bộ thông báo cũ của mọi phiên — kể cả những cái người dùng đã
    xem từ hôm trước.

    Lọc theo ``sessionId`` ở phía đọc. Stream là một kênh chung, nên không lọc
    thì phiên này nhận thông báo của phiên khác — rò rỉ vị trí giữa người dùng.
    """
    import time as _time

    if client is None:
        try:
            client = redis.Redis.from_url(
                settings.redis_url, decode_responses=True, socket_timeout=STREAM_SOCKET_TIMEOUT
            )
        except (redis.RedisError, OSError, ValueError):
            client = None
    if client is None:
        yield 'event: error\ndata: {"detail":"Redis không khả dụng"}\n\n'
        return

    started = _time.monotonic()
    last_id = "$"
    yield ": ket noi\n\n"
    while _time.monotonic() - started < STREAM_TTL_SECONDS:
        try:
            batch = client.xread(
                {NOTIFICATION_STREAM: last_id}, count=20, block=HEARTBEAT_SECONDS * 1000
            )
        except (redis.RedisError, OSError) as error:
            logger.warning("Đọc stream thông báo lỗi: %s", error)
            return
        if not batch:
            yield ": nhip tim\n\n"
            continue

        delivered: list[str] = []
        for _stream, entries in batch:
            for entry_id, fields in entries:
                last_id = entry_id
                try:
                    payload = json.loads(fields.get("payload") or "{}")
                except (ValueError, AttributeError):
                    continue
                if payload.get("sessionId") != session_id:
                    continue
                if payload.get("hitId"):
                    delivered.append(payload["hitId"])
                yield (
                    "event: proximity\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
        mark_notified(delivered)
