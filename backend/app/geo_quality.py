"""Lọc chất lượng toạ độ GPS trước khi cho vào tín hiệu hạ nguồn.

Trước module này, toàn bộ kiểm tra toạ độ chỉ là ràng buộc biên của Pydantic
(-90..90, -180..180): một thiết bị dùng fake GPS, một trình duyệt trả toạ độ
rác, hay một script bơm dữ liệu đều đi thẳng vào Redis GEO và vào
`search_sessions.last_location` mà không ai chặn.

Ba luật, xếp theo mức độ nghiêm trọng:

- **Ngoài vùng phục vụ** — toạ độ ngoài bbox TP.HCM. Loại thẳng: hệ thống không
  có POI nào ở đó nên ping cũng vô nghĩa.
- **Teleport** — tốc độ ngầm giữa ping này và ping trước vượt ngưỡng vật lý.
  Đây là dấu hiệu fake GPS rõ nhất: người thật không nhảy 50 km trong 2 giây.
- **Độ chính xác kém** — `accuracy_meters` quá lớn. KHÔNG loại, chỉ đánh dấu
  `low_quality`: toạ độ vẫn dùng được để biết người dùng đang ở khu vực nào,
  nhưng không đủ tin cậy để tính dwell hay bơm vào trending.

Toàn bộ là hàm thuần, không I/O, để test được mà không cần Postgres/Redis và để
tầng gọi tự quyết định làm gì với kết quả.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import settings


# Ngưỡng độ chính xác. GPS điện thoại ngoài trời thường 5–20 m; trên 150 m nghĩa
# là đang định vị bằng wifi/cell tower, sai số đó lớn hơn cả bán kính một POI.
MAX_ACCURACY_METERS = 150.0

# Tốc độ ngầm tối đa. 150 km/h cao hơn mọi phương tiện đường bộ thực tế ở TP.HCM
# nhưng vẫn dưới tốc độ máy bay, nên không đánh nhầm người đi ô tô trên cao tốc.
MAX_SPEED_KMH = 150.0

# Hai ping quá gần nhau về thời gian thì tốc độ tính ra không có ý nghĩa thống kê
# (sai số GPS vài chục mét chia cho 0,2 giây ra hàng trăm km/h). Bỏ qua luật
# teleport trong trường hợp này thay vì báo động giả.
MIN_INTERVAL_SECONDS = 1.0

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class GeoVerdict:
    """Kết luận về một toạ độ."""

    accepted: bool
    low_quality: bool
    reason: str | None = None
    speed_kmh: float | None = None

    def as_metadata(self) -> dict[str, Any]:
        """Phần ghi kèm vào `ingestion_events.metadata` để truy vết được."""
        data: dict[str, Any] = {"geo_accepted": self.accepted}
        if self.low_quality:
            data["geo_low_quality"] = True
        if self.reason:
            data["geo_reject_reason"] = self.reason
        if self.speed_kmh is not None:
            data["geo_speed_kmh"] = round(self.speed_kmh, 1)
        return data


def haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    lat_delta = math.radians(latitude_b - latitude_a)
    lng_delta = math.radians(longitude_b - longitude_a)
    a = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(latitude_a))
        * math.cos(math.radians(latitude_b))
        * math.sin(lng_delta / 2) ** 2
    )
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def within_service_area(latitude: float, longitude: float) -> bool:
    south, west, north, east = settings.osm_bbox_values
    return south <= latitude <= north and west <= longitude <= east


def implied_speed_kmh(
    latitude: float,
    longitude: float,
    previous_latitude: float,
    previous_longitude: float,
    seconds: float,
) -> float | None:
    """Tốc độ ngầm giữa hai ping, None nếu khoảng thời gian quá ngắn để tin được."""
    if seconds < MIN_INTERVAL_SECONDS:
        return None
    meters = haversine_meters(previous_latitude, previous_longitude, latitude, longitude)
    return (meters / seconds) * 3.6


def evaluate(
    latitude: float | None,
    longitude: float | None,
    accuracy_meters: float | None = None,
    previous: tuple[float, float, datetime] | None = None,
    occurred_at: datetime | None = None,
) -> GeoVerdict:
    """Chấm một toạ độ.

    `previous` là (vĩ độ, kinh độ, thời điểm) của ping trước cùng phiên, thường
    đọc từ `search_sessions`. Không có thì bỏ qua luật teleport.
    """
    if latitude is None or longitude is None:
        # Không có toạ độ không phải là lỗi: nhiều loại sự kiện không kèm vị trí.
        return GeoVerdict(accepted=True, low_quality=False)

    if not within_service_area(latitude, longitude):
        return GeoVerdict(accepted=False, low_quality=True, reason="outside_service_area")

    speed = None
    if previous and occurred_at:
        previous_latitude, previous_longitude, previous_at = previous
        seconds = (occurred_at - previous_at).total_seconds()
        if seconds >= 0:
            speed = implied_speed_kmh(
                latitude, longitude, previous_latitude, previous_longitude, seconds
            )
            if speed is not None and speed > MAX_SPEED_KMH:
                return GeoVerdict(
                    accepted=False,
                    low_quality=True,
                    reason="teleport",
                    speed_kmh=speed,
                )

    low_quality = accuracy_meters is not None and accuracy_meters > MAX_ACCURACY_METERS
    return GeoVerdict(accepted=True, low_quality=low_quality, speed_kmh=speed)
