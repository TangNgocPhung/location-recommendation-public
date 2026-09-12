"""Geo-Spatial Cache (Redis) — tầng 2 của sơ đồ kiến trúc.

Ba lỗ hổng của khối này nằm cùng một chỗ nên sửa cùng một lần (lộ trình B6):

**(a) Trending toàn cục.** ``stream_worker`` chỉ ZINCRBY vào hai key toàn cục
dù biến ``location`` đang có sẵn ngay dòng bên cạnh. Hệ quả: quán trà sữa đang
hot ở Quận 1 được cộng điểm cho cả người đang tìm ở Thủ Đức. Nay mỗi lượt
tương tác còn được ghi thêm vào ô H3 của **người dùng lúc đó**, và phía đọc ưu
tiên ô quanh tâm tìm kiếm.

**(b) Bộ đếm không bao giờ quên.** Hai sorted set cũ không có EXPIRE, không có
ZREMRANGEBYSCORE, không reset — điểm cộng dồn vĩnh viễn từ lúc khởi động, nên
sau vài ngày "trending" đóng băng ở những POI cũ. Nay mọi bộ đếm đều nằm trong
**khung giờ** ``:{YYYYMMDDHH}`` có TTL, và phía đọc gộp ba khung gần nhất với
hệ số 1.0 / 0.5 / 0.25 — trending có biên thời gian trả lời được câu hỏi
"trending trong bao lâu?".

**(c) GEOADD ghi rồi bỏ đó.** ``nearby:active-locations`` được ghi từ đầu
nhưng chưa lệnh nào đọc. Nay ``active_session_points`` dùng GEOSEARCH để đếm
phiên đang hoạt động quanh một điểm — tín hiệu độ đông thời gian thực lấy từ
chính dữ liệu đang bị bỏ phí.

Mọi hàm đọc đều nuốt lỗi Redis và trả giá trị rỗng: Redis chết thì tín hiệu
trending/độ đông tắt, chứ không được làm hỏng cả truy vấn tìm kiếm.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import h3
import redis

from .config import settings

logger = logging.getLogger("nearby-geo-cache")

REDIS_URL = settings.redis_url

TRENDING_POIS_PREFIX = "nearby:trending:pois"
TRENDING_QUERIES_PREFIX = "nearby:trending:queries"
TRENDING_HEX_PREFIX = "nearby:trending:hex"
ACTIVE_LOCATIONS_KEY = "nearby:active-locations"

# Độ phân giải ô cho trending theo khu vực. r8 ~ 0,74 km² — cỡ vài dãy phố,
# đủ nhỏ để "quanh đây" có nghĩa và đủ lớn để không rỗng sau vài chục sự kiện.
TRENDING_RESOLUTION = 8

# Bán kính vành ô khi ĐỌC. k=1 là ô của tâm tìm kiếm cộng 6 ô kề (~5 km²).
# Chỉ đọc đúng một ô thì một bước chân qua ranh giới ô cũng đổi hẳn kết quả.
TRENDING_READ_RING = 1

# Trọng số ba khung giờ gần nhất khi gộp. Giờ hiện tại tính đủ, giờ trước một
# nửa, giờ trước nữa một phần tư.
WINDOW_WEIGHTS: tuple[float, ...] = (1.0, 0.5, 0.25)

# TTL của một khung giờ. Phải dài hơn cửa sổ đọc (3 giờ) để khung cũ nhất còn
# sống lúc được gộp; cộng thêm một giờ đệm cho lệch đồng hồ.
BUCKET_TTL_SECONDS = 4 * 3600

# Bán kính coi là "quanh POI" khi đếm phiên đang hoạt động.
CROWD_RADIUS_METERS = 300.0


def get_client() -> redis.Redis | None:
    try:
        return redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1.5)
    except (redis.RedisError, OSError, ValueError):
        return None


# --- Khung giờ ----------------------------------------------------------------


def hour_bucket(at: datetime | None = None) -> str:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y%m%d%H")


def recent_buckets(at: datetime | None = None, count: int | None = None) -> list[str]:
    """Khung giờ hiện tại rồi lùi dần, mặc định đủ số khung của cửa sổ đọc."""
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    total = len(WINDOW_WEIGHTS) if count is None else count
    return [hour_bucket(moment - timedelta(hours=offset)) for offset in range(total)]


def poi_key(bucket: str) -> str:
    return f"{TRENDING_POIS_PREFIX}:{bucket}"


def query_key(bucket: str) -> str:
    return f"{TRENDING_QUERIES_PREFIX}:{bucket}"


def hex_key(cell: str, bucket: str) -> str:
    return f"{TRENDING_HEX_PREFIX}:{cell}:{bucket}"


def trending_cell(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    try:
        return h3.latlng_to_cell(float(latitude), float(longitude), TRENDING_RESOLUTION)
    except (TypeError, ValueError):
        return None


# --- Phía GHI (stream worker) --------------------------------------------------


def record_poi_interaction(
    pipeline: Any,
    poi_id: str,
    weight: float,
    latitude: float | None = None,
    longitude: float | None = None,
    at: datetime | None = None,
) -> str | None:
    """Cộng điểm trending cho một POI vào khung giờ hiện tại.

    Ghi hai nơi: bộ đếm toàn cục (để còn chỗ rơi về khi một ô chưa đủ dữ liệu)
    và bộ đếm theo ô H3 của **người dùng lúc tương tác**. Trả về mã ô đã ghi,
    hoặc None khi sự kiện không kèm toạ độ dùng được.
    """
    bucket = hour_bucket(at)
    key = poi_key(bucket)
    pipeline.zincrby(key, weight, poi_id)
    pipeline.expire(key, BUCKET_TTL_SECONDS)

    cell = trending_cell(latitude, longitude)
    if cell is None:
        return None
    cell_key = hex_key(cell, bucket)
    pipeline.zincrby(cell_key, weight, poi_id)
    pipeline.expire(cell_key, BUCKET_TTL_SECONDS)
    return cell


def record_query(
    pipeline: Any, query_text: str, weight: float = 1.0, at: datetime | None = None
) -> None:
    bucket = hour_bucket(at)
    key = query_key(bucket)
    pipeline.zincrby(key, weight, query_text)
    pipeline.expire(key, BUCKET_TTL_SECONDS)


# --- Phía ĐỌC -----------------------------------------------------------------


def _weighted_sum(client: Any, keys_by_weight: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Gộp nhiều sorted set thành một bảng điểm, mỗi set nhân một hệ số.

    Gộp trong Python thay vì ZUNIONSTORE: cách này không ghi gì vào Redis (không
    sinh key tạm phải dọn) và số key cần gộp ở đây nhỏ — nhiều nhất là 7 ô × 3
    khung giờ. Đổi lại phải kéo toàn bộ thành viên của từng khung về, nên các
    khung giờ được giữ TTL ngắn để chúng không phình.
    """
    if not keys_by_weight:
        return {}
    try:
        pipeline = client.pipeline(transaction=False)
        for key, _weight in keys_by_weight:
            pipeline.zrange(key, 0, -1, withscores=True)
        responses = pipeline.execute()
    except (redis.RedisError, OSError, AttributeError):
        return {}

    totals: dict[str, float] = {}
    for (_key, weight), entries in zip(keys_by_weight, responses):
        for member, score in entries or []:
            try:
                totals[member] = totals.get(member, 0.0) + weight * float(score)
            except (TypeError, ValueError):
                continue
    return totals


def _window_keys(key_fn, at: datetime | None = None) -> list[tuple[str, float]]:
    return [
        (key_fn(bucket), weight) for bucket, weight in zip(recent_buckets(at), WINDOW_WEIGHTS)
    ]


def trending_pois(client: Any | None = None, at: datetime | None = None) -> dict[str, float]:
    """Bảng điểm trending TOÀN CỤC đã gộp ba khung giờ gần nhất."""
    client = client or get_client()
    if client is None:
        return {}
    return _weighted_sum(client, _window_keys(poi_key, at))


def trending_queries(client: Any | None = None, at: datetime | None = None) -> dict[str, float]:
    client = client or get_client()
    if client is None:
        return {}
    return _weighted_sum(client, _window_keys(query_key, at))


def trending_pois_near(
    latitude: float,
    longitude: float,
    client: Any | None = None,
    at: datetime | None = None,
) -> dict[str, float]:
    """Bảng điểm trending của KHU VỰC quanh một toạ độ.

    Gộp vành ô bán kính ``TRENDING_READ_RING`` quanh ô chứa toạ độ, mỗi ô ba
    khung giờ. Rỗng là chuyện bình thường ở vùng ít người dùng — gọi bên phải
    tự rơi về bảng toàn cục.
    """
    client = client or get_client()
    if client is None:
        return {}
    cell = trending_cell(latitude, longitude)
    if cell is None:
        return {}
    cells = h3.grid_disk(cell, TRENDING_READ_RING)
    keys: list[tuple[str, float]] = []
    for bucket, weight in zip(recent_buckets(at), WINDOW_WEIGHTS):
        keys.extend((hex_key(one, bucket), weight) for one in cells)
    return _weighted_sum(client, keys)


# --- (c) GEOSEARCH trên dữ liệu GEOADD đang bị bỏ phí ---------------------------


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def active_session_points(
    latitude: float,
    longitude: float,
    radius_m: float,
    client: Any | None = None,
) -> list[tuple[float, float]]:
    """Toạ độ các phiên đang hoạt động trong bán kính, qua GEOSEARCH.

    MỘT lệnh Redis cho cả truy vấn, rồi đếm quanh từng POI trong Python — gọi
    GEOSEARCH riêng cho mỗi ứng viên thì một truy vấn 300 ứng viên thành 300
    vòng đi-về.
    """
    client = client or get_client()
    if client is None:
        return []
    try:
        rows = client.geosearch(
            ACTIVE_LOCATIONS_KEY,
            longitude=longitude,
            latitude=latitude,
            radius=radius_m,
            unit="m",
            withcoord=True,
        )
    except (redis.RedisError, OSError, AttributeError) as error:
        logger.debug("GEOSEARCH lỗi, bỏ tín hiệu độ đông: %s", error)
        return []

    points: list[tuple[float, float]] = []
    for row in rows or []:
        # geosearch(withcoord=True) trả [member, (lon, lat)] — chú ý THỨ TỰ:
        # Redis trả kinh độ trước, ngược với quy ước (lat, lon) của phần còn lại.
        try:
            coord = row[1]
            points.append((float(coord[1]), float(coord[0])))
        except (TypeError, ValueError, IndexError):
            continue
    return points


def count_near(
    points: Iterable[tuple[float, float]],
    latitude: float,
    longitude: float,
    radius_m: float = CROWD_RADIUS_METERS,
) -> int:
    return sum(
        1 for lat, lon in points if haversine_meters(lat, lon, latitude, longitude) <= radius_m
    )
