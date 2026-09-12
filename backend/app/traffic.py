"""Traffic Density Injection cho Spatio-Temporal Enricher (lộ trình B12b).

Không gọi API giao thông nào. Mật độ được suy ra từ **chính luồng GPS ping của
hệ thống** — dữ liệu đã nằm sẵn trong ``ingestion_events`` và trước bước này
chưa ai đọc để làm gì ngoài việc lưu.

Hai thành phần, cộng lại thành một hệ số nhân vào thời gian di chuyển:

**(a) Giờ cao điểm** — 7:00–8:30 và 16:30–19:00 giờ địa phương. Tất định, có
ngay cả khi hệ thống chưa có một người dùng nào, nên nó là phần *luôn* chạy.

**(b) Mật độ tương đối theo ô H3 r8** — đếm ``location_ping`` trong 30 phút gần
nhất, nhóm theo ô, rồi chuẩn hóa theo ô đông nhất trong chính lượt truy vấn đó.
Chuẩn hóa tương đối chứ không theo ngưỡng tuyệt đối: một hệ thống đang có 5
người dùng và một hệ thống có 5000 người không thể dùng chung một con số "bao
nhiêu ping thì gọi là đông".

Hệ số CHỈ áp cho xe máy và ô tô. Người đi bộ không kẹt xe — nhân hệ số tắc
đường vào thời gian đi bộ là một con số sai một cách rất dễ thấy khi bảo vệ.

Trường ``trafficSource`` đi kèm mọi kết quả để phân biệt "đã tính cả mật độ
thật" với "mới chỉ có hệ số giờ cao điểm": với log rỗng thì (b) bằng 0 ở khắp
nơi, và nếu không ghi ra thì bảng số trong báo cáo sẽ được đọc nhầm thành
"mật độ giao thông không ảnh hưởng gì".
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import h3
import psycopg
from psycopg.rows import dict_row

from .config import settings

logger = logging.getLogger("nearby-traffic")

DATABASE_URL = settings.database_url
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"

# Độ phân giải ô đếm mật độ. r8 ~ 0,74 km² — cỡ một cụm vài dãy phố, đủ nhỏ để
# phân biệt được một nút giao đông với khu dân cư kế bên.
DENSITY_RESOLUTION = 8
DENSITY_WINDOW_MINUTES = 30

# Khung giờ cao điểm TP.HCM, theo giờ địa phương, dạng (giờ_phút_bắt_đầu,
# giờ_phút_kết_thúc) tính bằng phút từ 0:00.
PEAK_WINDOWS: tuple[tuple[int, int], ...] = (
    (7 * 60, 8 * 60 + 30),
    (16 * 60 + 30, 19 * 60),
)

# Vận tốc giờ cao điểm còn lại bao nhiêu phần so với giờ thường.
PEAK_SPEED_RATIO = 0.65

# Ô đông nhất bị cộng thêm tối đa bằng này phần thời gian di chuyển.
MAX_DENSITY_PENALTY = 0.25

# Phương tiện chịu ảnh hưởng của giao thông.
AFFECTED_MODES = ("motorbike", "car")


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def is_peak_hour(at: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> bool:
    zone = _zone(timezone_name)
    now = (at or datetime.now(zone)).astimezone(zone)
    minutes = now.hour * 60 + now.minute
    return any(start <= minutes < end for start, end in PEAK_WINDOWS)


def peak_speed_ratio(at: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> float:
    """Phần vận tốc còn lại so với giờ thường (1.0 = không ảnh hưởng)."""
    return PEAK_SPEED_RATIO if is_peak_hour(at, timezone_name) else 1.0


def ping_density(database_url: str | None = None) -> dict[str, int]:
    """Số ``location_ping`` trong 30 phút gần nhất, nhóm theo ô H3 r8.

    ``ingestion_events`` không có cột h3 — chỉ có ``location`` kiểu geography.
    Nên lấy toạ độ về rồi quy ra ô trong Python: cửa sổ 30 phút giữ số dòng
    nhỏ, và làm vậy thì độ phân giải đổi được mà không phải thêm cột hay viết
    migration.
    """
    query = f"""
        SELECT ST_Y(location::geometry) AS latitude,
               ST_X(location::geometry) AS longitude
        FROM ingestion_events
        WHERE event_type = 'location_ping'
          AND location IS NOT NULL
          AND occurred_at > NOW() - INTERVAL '{DENSITY_WINDOW_MINUTES} minutes'
    """
    try:
        with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
    except psycopg.Error as error:
        logger.debug("Không đọc được mật độ ping, bỏ thành phần này: %s", error)
        return {}

    counts: dict[str, int] = {}
    for row in rows:
        latitude, longitude = row.get("latitude"), row.get("longitude")
        if latitude is None or longitude is None:
            continue
        try:
            cell = h3.latlng_to_cell(float(latitude), float(longitude), DENSITY_RESOLUTION)
        except (TypeError, ValueError):
            continue
        counts[cell] = counts.get(cell, 0) + 1
    return counts


def density_penalty(cell: str | None, counts: dict[str, int]) -> float:
    """Phần thời gian cộng thêm do mật độ, trong ``[0, MAX_DENSITY_PENALTY]``.

    Chuẩn hóa theo ô đông nhất đang quan sát được. Chỉ có một ô có dữ liệu thì
    mọi ô đều không bị phạt: một điểm dữ liệu không nói lên ô nào đông hơn ô nào.
    """
    if not cell or len(counts) < 2:
        return 0.0
    busiest = max(counts.values())
    if busiest <= 0:
        return 0.0
    return round(MAX_DENSITY_PENALTY * (counts.get(cell, 0) / busiest), 6)


def traffic_factor(
    latitude: float | None,
    longitude: float | None,
    counts: dict[str, int],
    at: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Hệ số nhân vào THỜI GIAN di chuyển (>= 1.0) kèm phần giải thích.

    Tách phần giờ cao điểm và phần mật độ trong kết quả, vì hai phần này có độ
    tin cậy rất khác nhau: giờ cao điểm là quy ước, mật độ là số đo.
    """
    ratio = peak_speed_ratio(at, timezone_name)
    cell = None
    if latitude is not None and longitude is not None:
        try:
            cell = h3.latlng_to_cell(float(latitude), float(longitude), DENSITY_RESOLUTION)
        except (TypeError, ValueError):
            cell = None
    penalty = density_penalty(cell, counts)
    factor = round((1.0 / ratio) * (1.0 + penalty), 6)
    return {
        "factor": factor,
        "isPeakHour": ratio < 1.0,
        "densityPenalty": penalty,
        "cell": cell,
        "source": "peak+density" if counts else "peak-only",
    }


def apply_to_eta(eta: dict[str, int] | None, factor: float) -> dict[str, int] | None:
    """Nhân hệ số vào ETA của xe máy/ô tô, giữ nguyên thời gian đi bộ."""
    if not eta:
        return eta
    adjusted = dict(eta)
    for mode in AFFECTED_MODES:
        if mode in adjusted:
            adjusted[mode] = max(1, round(adjusted[mode] * factor))
    return adjusted
