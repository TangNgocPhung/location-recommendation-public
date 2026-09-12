"""Spatio-Temporal Enricher (Bước 5).

Làm giàu mỗi candidate bằng ngữ cảnh không gian–thời gian *trước khi* xếp hạng,
đúng khối "Spatio-Temporal Enricher" trong sơ đồ. Toàn bộ dùng dữ liệu sẵn có
(giờ mở, event log) + đồng hồ hệ thống, cộng đúng MỘT lần gọi API ngoài cho
thời tiết tại tâm truy vấn:

- Trạng thái giờ mở: đang mở / sắp đóng / sắp mở (từ ``opening_hours``).
- Thời điểm: buổi trong ngày + ngày thường/cuối tuần, và mức phù hợp của loại
  địa điểm với thời điểm đó (cà phê buổi sáng, quán bar buổi tối…).
- Popularity theo cửa sổ 15 phút / 1 giờ / 24 giờ từ ``ingestion_events`` với
  trọng số thiên về gần đây (time-decay).
- ETA ước lượng theo phương tiện (đi bộ/xe máy/ô tô) từ khoảng cách, có nhân
  hệ số giao thông (giờ cao điểm + mật độ ping theo ô H3).
- Thời tiết tại tâm truy vấn (Open-Meteo), nhân nhẹ vào ``contextScore``.

Kết quả được nén thành ``contextScore`` và ``recencyScore`` trong ``[0,1]`` để
``ranking.rerank`` dùng như tín hiệu bổ sung, cùng các trường hiển thị cho UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row

from . import traffic, weather
from .config import settings
from .opening_hours import opening_status

DATABASE_URL = settings.database_url

# Loại sự kiện được tính là "quan tâm thật" cho recencyScore.
#
# 'poi_impression' CỐ Ý bị loại. Sau bước A2 nó là impression thật: mỗi POI được
# render sinh một sự kiện, tức ~50 impression cho mỗi click. Nếu đếm chúng thì
# raw_recency trở thành "POI này được hiển thị bao nhiêu lần" — mà số lần hiển
# thị lại do CHÍNH xếp hạng quyết định, tạo vòng lặp tự củng cố: xếp cao → được
# hiển thị → recency cao → xếp cao hơn nữa. Trọng số recency là 0.12, còn lớn
# hơn ctr 0.08. Tệ hơn, một lần tìm kiếm bơm 50 sự kiện vào cửa sổ 15 phút nên
# lần tìm kiếm ngay sau đó của cùng người dùng đã bị thiên lệch.
#
# 'poi_dwell' thay chỗ: ở lại lâu là tín hiệu quan tâm do người dùng chủ động
# tạo ra, không phải do hệ thống tự bơm.
POPULARITY_EVENT_TYPES = ("poi_click", "poi_dwell", "navigation_start", "review")
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"

# --- Thời điểm trong ngày -------------------------------------------------

# (giờ_bắt_đầu, giờ_kết_thúc] theo giờ địa phương; "night" ôm qua nửa đêm.
_TIME_BUCKETS = [
    ("morning", 5, 11),
    ("noon", 11, 14),
    ("afternoon", 14, 17),
    ("evening", 17, 22),
]

# Loại địa điểm phù hợp với buổi nào. Loại không liệt kê => trung tính (không
# thưởng, không phạt), phù hợp cho y tế/dịch vụ vốn không lệ thuộc thời điểm.
CATEGORY_TIME_AFFINITY: dict[str, set[str]] = {
    "cafe": {"morning", "noon", "afternoon"},
    "bakery": {"morning", "noon"},
    "restaurant": {"noon", "evening"},
    "bar": {"evening", "night"},
    "cinema": {"evening", "night"},
    "park": {"morning", "afternoon", "evening"},
    "gym": {"morning", "evening"},
    "market": {"morning"},
    "supermarket": {"afternoon", "evening"},
    "shopping_mall": {"afternoon", "evening"},
    "museum": {"noon", "afternoon"},
    "landmark": {"morning", "afternoon"},
}

# Vận tốc di chuyển trung bình trong đô thị (km/h) để ước lượng ETA thô.
_MODE_SPEED_KMH = {"walk": 4.8, "motorbike": 22.0, "car": 26.0}

# Hệ số vòng vèo: đường đi thật dài hơn đường chim bay bao nhiêu lần.
#
# ĐO THẬT, không phải con số kinh nghiệm. `scripts/measure_detour.py` chạy 477
# cặp (4 tâm truy vấn × 120 POI trong bán kính 5 km) qua OSRM tự dựng trên dữ
# liệu đường phố TP.HCM:
#
#     trung vị 1.4625 · trung bình 1.557 · p10 1.192 · p90 2.090
#
# Lấy TRUNG VỊ chứ không lấy trung bình: phân bố lệch phải mạnh (max 3.54 với
# những POI bị sông hoặc đường một chiều chắn), nên trung bình bị vài trường
# hợp cực đoan kéo lên và sẽ làm ETA của đa số POI dài quá mức.
#
# Lộ trình đề xuất "hệ số kinh nghiệm 1.35"; số đo thật cao hơn khoảng 8%.
# Kết quả đầy đủ: backend/results/detour_factor_*.json
#
# Lưu ý: đo trên hồ sơ ô tô của OSRM. Người đi bộ cắt được ngõ hẻm nên hệ số
# thật của họ thấp hơn — con số này vì vậy là cận trên cho chế độ đi bộ.
DETOUR_FACTOR = 1.4625


def time_context(at: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    now = (at or datetime.now(zone)).astimezone(zone)
    hour = now.hour
    bucket = "night"
    for name, start, end in _TIME_BUCKETS:
        if start <= hour < end:
            bucket = name
            break
    return {"bucket": bucket, "isWeekend": now.weekday() >= 5, "hour": hour}


def category_time_match(category: str | None, bucket: str) -> float | None:
    """1.0 nếu loại địa điểm hợp thời điểm, 0.0 nếu có ưu tiên nhưng lệch,
    None nếu loại không có ưu tiên thời điểm (trung tính)."""
    preferred = CATEGORY_TIME_AFFINITY.get(category or "")
    if not preferred:
        return None
    return 1.0 if bucket in preferred else 0.0


def eta_minutes(distance_meters: float | None) -> dict[str, int] | None:
    """Ước lượng thời gian di chuyển từ khoảng cách ĐƯỜNG CHIM BAY.

    Nhân ``DETOUR_FACTOR`` vì không ai đi được theo đường thẳng. Thiếu hệ số
    này thì mọi ETA đều lạc quan khoảng 46% và người dùng đến muộn — mà sai số
    đó lại không lộ ra ở đâu cả, vì con số trông vẫn rất hợp lý.

    Đây vẫn chỉ là ƯỚC LƯỢNG. POI đang chọn dùng thời gian thật của OSRM
    (``app.directions``); hàm này phục vụ cả danh sách kết quả, nơi gọi OSRM
    cho từng POI sẽ là hàng trăm lần tính tuyến cho một lần tìm kiếm.
    """
    if distance_meters is None:
        return None
    km = max(distance_meters, 0.0) / 1000.0 * DETOUR_FACTOR
    return {
        mode: max(1, round(km / speed * 60))
        for mode, speed in _MODE_SPEED_KMH.items()
    }


def windowed_popularity(
    ids: list[str], database_url: str | None = None
) -> dict[str, dict[str, int]]:
    """Đếm tương tác theo cửa sổ 15p/1h/24h cho từng POI trong một truy vấn."""
    if not ids:
        return {}
    query = """
        SELECT poi_id,
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '15 minutes')::int AS w15,
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '60 minutes')::int AS w1h,
            COUNT(*)::int AS w24h
        FROM ingestion_events
        WHERE poi_id = ANY(%(ids)s)
          AND event_type = ANY(%(event_types)s)
          AND occurred_at > NOW() - INTERVAL '24 hours'
        GROUP BY poi_id
    """
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, {"ids": ids, "event_types": list(POPULARITY_EVENT_TYPES)})
            return {
                row["poi_id"]: {"w15": row["w15"], "w1h": row["w1h"], "w24h": row["w24h"]}
                for row in cursor.fetchall()
            }


def _context_score(open_now: bool | None, closes_in: int | None, time_match: float | None) -> float:
    # Thành phần giờ mở (giữ trung tính 0.5 khi chưa rõ để không phạt oan).
    if open_now is True:
        open_component = 0.6 if (closes_in is not None and closes_in < 30) else 1.0
    elif open_now is False:
        open_component = 0.0
    else:
        open_component = 0.5
    # Thành phần thời điểm: hợp thời điểm = 1.0; loại không có ưu tiên = 0.5
    # (trung tính); lệch thời điểm = 0.35 (giảm nhẹ, không phạt nặng).
    if time_match is None:
        time_component = 0.5
    elif time_match >= 1.0:
        time_component = 1.0
    else:
        time_component = 0.35
    return round(0.6 * open_component + 0.4 * time_component, 6)


def enrich_candidates(
    candidates: list[dict[str, Any]],
    at: datetime | None = None,
    database_url: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> list[dict[str, Any]]:
    """Gắn ngữ cảnh không gian–thời gian và các điểm ``contextScore`` /
    ``recencyScore`` (đã chuẩn hóa ``[0,1]``) vào từng candidate.

    ``latitude``/``longitude`` là TÂM TRUY VẤN, không phải toạ độ của một POI:
    thời tiết và mật độ giao thông được lấy một lần cho cả lượt tìm kiếm. Thiếu
    tâm truy vấn thì hai tín hiệu đó tắt và mọi hệ số bằng 1.0 — kết quả y hệt
    như trước khi có bước B12.
    """
    if not candidates:
        return candidates

    ctx = time_context(at)

    # Một lần gọi cho cả lượt truy vấn, không phải một lần cho mỗi ứng viên.
    conditions = None
    if settings.weather_enabled and latitude is not None and longitude is not None:
        conditions = weather.current_weather(latitude, longitude, at=at)
    density = traffic.ping_density(database_url) if settings.traffic_enabled else {}
    ids = [candidate["id"] for candidate in candidates]
    try:
        popularity = windowed_popularity(ids, database_url)
    except psycopg.Error:
        popularity = {}

    # Điểm recency thô, thiên về gần đây; chuẩn hóa theo max trong tập ứng viên.
    raw_recency: dict[str, float] = {}
    for poi_id, windows in popularity.items():
        raw_recency[poi_id] = 3.0 * windows["w15"] + 1.5 * windows["w1h"] + 0.5 * windows["w24h"]
    max_recency = max(raw_recency.values(), default=0.0)

    for candidate in candidates:
        status = opening_status(
            candidate.get("openingHours"), candidate.get("timezone") or DEFAULT_TIMEZONE, at
        )
        candidate["openNow"] = status["openNow"]
        candidate["closesInMinutes"] = status["closesInMinutes"]
        candidate["opensInMinutes"] = status["opensInMinutes"]

        time_match = category_time_match(candidate.get("category"), ctx["bucket"])
        candidate["timeContext"] = {**ctx, "categoryMatchesTime": time_match}

        road = traffic.traffic_factor(
            candidate.get("latitude"), candidate.get("longitude"), density, at=at
        )
        candidate["traffic"] = road
        candidate["etaMinutes"] = traffic.apply_to_eta(
            eta_minutes(candidate.get("distanceMeters")), road["factor"]
        )

        windows = popularity.get(candidate["id"], {"w15": 0, "w1h": 0, "w24h": 0})
        candidate["popularityWindows"] = windows
        raw = raw_recency.get(candidate["id"], 0.0)
        candidate["recencyScore"] = round(raw / max_recency, 6) if max_recency else 0.0

        base_context = _context_score(
            status["openNow"], status["closesInMinutes"], time_match
        )
        factor = weather.weather_factor(candidate.get("category"), conditions)
        # Kẹp về [0,1]: `contextScore` đi thẳng vào tổng có trọng số của
        # `rerank`, và một số hạng vượt 1.0 sẽ làm điểm cuối vượt khỏi thang
        # [0,1] — đúng loại lỗi mà bước sửa `rating` NULL đã phải dọn một lần.
        candidate["contextScore"] = round(min(1.0, max(0.0, base_context * factor)), 6)
        candidate["weatherFactor"] = factor
        candidate["weather"] = conditions
    return candidates
