"""Weather Injection cho Spatio-Temporal Enricher (lộ trình B12a).

Ô "Weather & Traffic Density Injection" trong sơ đồ tầng 3 trước đây không có
một dòng code nào — grep ``weather|traffic|congestion`` toàn repo chỉ trúng một
dòng thừa nhận trong tài liệu.

Thiết kế, theo thứ tự các ràng buộc quan trọng nhất:

**Một truy vấn tìm kiếm = nhiều nhất một lần gọi API ngoài.** Thời tiết lấy cho
duy nhất tâm truy vấn, không phải cho từng POI. Trong bán kính vài km thì trời
mưa ở POI này cũng là mưa ở POI kia; gọi theo từng ứng viên là 300 lần gọi cho
một thông tin duy nhất.

**Không bao giờ làm hỏng truy vấn.** Open-Meteo không cần API key nhưng vẫn là
mạng ngoài: timeout 1,5 giây, mọi lỗi trả ``None``, và ``None`` nghĩa là hệ số
1.0 ở mọi nơi — tức xếp hạng y hệt như trước khi có module này. Cùng cách
``_trending_ids`` xử lý khi Redis chết.

**Dùng thư viện chuẩn.** ``urllib.request`` thay vì thêm một phụ thuộc mới vào
ảnh runtime chỉ để gọi một endpoint GET.

Cache Redis theo ô H3 r7 (~5 km²) + khung giờ, TTL 30 phút: mọi người dùng
trong cùng một vùng và cùng một giờ dùng chung một lần gọi.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import h3
import redis

from .config import settings

logger = logging.getLogger("nearby-weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_PREFIX = "nearby:weather"
CACHE_TTL_SECONDS = 30 * 60
CACHE_RESOLUTION = 7
REQUEST_TIMEOUT_SECONDS = 1.5

# Mã thời tiết WMO mà Open-Meteo trả về trong `weather_code`.
# https://open-meteo.com/en/docs — bảng "Weather variable documentation".
_DRIZZLE = {51, 53, 55, 56, 57}
_RAIN = {61, 63, 65, 66, 67}
_SHOWERS = {80, 81, 82}
_THUNDERSTORM = {95, 96, 99}
_HEAVY_CODES = {65, 67, 82} | _THUNDERSTORM
WET_CODES = _DRIZZLE | _RAIN | _SHOWERS | _THUNDERSTORM

# Lượng mưa (mm/giờ) từ mức này trở lên thì coi là mưa to kể cả khi mã thời tiết
# nói nhẹ hơn — hai nguồn cùng một hiện tượng, lấy cái nào nghiêm trọng hơn.
HEAVY_PRECIPITATION_MM = 2.5

# Mức che chắn của từng loại địa điểm khi trời mưa.
#
# Bảng này KHÔNG suy ra được từ `CATEGORY_TIME_AFFINITY`: cái kia nói địa điểm
# hợp buổi nào trong ngày, không nói nó có mái che hay không. Danh sách loại
# lấy đúng từ `poi_features.CATEGORY_MAP` — toàn bộ 33 loại thật đang có trong
# dữ liệu, không bịa thêm loại nào.
#
# "necessity" là nhóm quan trọng nhất phải tách riêng: không ai hoãn đi bệnh
# viện vì trời mưa, nên hạ điểm bệnh viện lúc mưa là một hành vi sai chứ không
# phải một tinh chỉnh.
OUTDOOR_CATEGORIES = frozenset({"park", "playground", "landmark", "market"})
INDOOR_CATEGORIES = frozenset(
    {
        "cafe",
        "restaurant",
        "bar",
        "bakery",
        "cinema",
        "theatre",
        "library",
        "museum",
        "gallery",
        "shopping_mall",
        "supermarket",
        "convenience",
        "bookstore",
        "clothes",
        "electronics",
        "hotel",
        "bank",
        "gym",
    }
)
NECESSITY_CATEGORIES = frozenset({"hospital", "pharmacy", "school", "university", "atm"})

# Hệ số nhân vào `contextScore`. Cố ý nhẹ: thời tiết là ngữ cảnh, không phải
# mức độ liên quan — trời mưa không làm một quán cà phê thành kết quả sai cho
# truy vấn "công viên".
RAIN_OUTDOOR_FACTOR = 0.80
RAIN_INDOOR_FACTOR = 1.10
HEAVY_RAIN_OUTDOOR_FACTOR = 0.65
HEAVY_RAIN_INDOOR_FACTOR = 1.15


def cache_key(latitude: float, longitude: float, at: datetime | None = None) -> str:
    cell = h3.latlng_to_cell(latitude, longitude, CACHE_RESOLUTION)
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{CACHE_PREFIX}:{cell}:{moment.strftime('%Y%m%d%H')}"


def classify(payload: dict[str, Any]) -> dict[str, Any]:
    """Đổi dữ liệu thô Open-Meteo thành kết luận dùng được cho xếp hạng."""
    try:
        code = int(payload.get("weather_code"))
    except (TypeError, ValueError):
        code = -1
    try:
        precipitation = float(payload.get("precipitation") or 0.0)
    except (TypeError, ValueError):
        precipitation = 0.0

    is_wet = code in WET_CODES or precipitation > 0.0
    is_heavy = code in _HEAVY_CODES or precipitation >= HEAVY_PRECIPITATION_MM
    return {
        "weatherCode": code,
        "precipitationMm": precipitation,
        "temperatureC": payload.get("temperature_2m"),
        "windSpeedKmh": payload.get("wind_speed_10m"),
        "isWet": is_wet,
        "isHeavyRain": bool(is_wet and is_heavy),
        "observedAt": payload.get("time"),
    }


def _fetch_remote(latitude: float, longitude: float) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
            "timezone": "UTC",
        }
    )
    try:
        with urllib.request.urlopen(
            f"{OPEN_METEO_URL}?{query}", timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as error:
        # Mức debug chứ không phải warning: mất mạng là chuyện thường và tín
        # hiệu này chỉ là gia vị. Nâng lên warning thì log sẽ đầy rác mỗi lần
        # chạy offline, và cảnh báo thật bị chìm theo.
        logger.debug("Không lấy được thời tiết, bỏ tín hiệu lần này: %s", error)
        return None

    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    return classify(current)


def current_weather(
    latitude: float,
    longitude: float,
    client: Any | None = None,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    """Thời tiết hiện tại tại một toạ độ, ưu tiên cache Redis.

    Trả ``None`` khi không lấy được — gọi bên phải coi đó là "không có tín
    hiệu" chứ không phải "trời đẹp".
    """
    key = cache_key(latitude, longitude, at)
    if client is None:
        try:
            client = redis.Redis.from_url(
                settings.redis_url, decode_responses=True, socket_timeout=1.5
            )
        except (redis.RedisError, OSError, ValueError):
            client = None

    if client is not None:
        try:
            cached = client.get(key)
            if cached:
                return json.loads(cached)
        except (redis.RedisError, OSError, ValueError, json.JSONDecodeError):
            pass

    weather = _fetch_remote(latitude, longitude)
    if weather is None:
        return None

    if client is not None:
        try:
            client.setex(key, CACHE_TTL_SECONDS, json.dumps(weather))
        except (redis.RedisError, OSError, TypeError):
            pass
    return weather


def weather_factor(category: str | None, weather: dict[str, Any] | None) -> float:
    """Hệ số nhân vào ``contextScore`` theo mức che chắn của loại địa điểm.

    1.0 nghĩa là không đổi, và đó là giá trị trả về trong MỌI trường hợp không
    chắc chắn: không có dữ liệu thời tiết, trời khô, loại địa điểm thiết yếu,
    hoặc loại không nằm trong bảng.
    """
    if not weather or not weather.get("isWet"):
        return 1.0
    key = (category or "").strip()
    if key in NECESSITY_CATEGORIES:
        return 1.0
    heavy = bool(weather.get("isHeavyRain"))
    if key in OUTDOOR_CATEGORIES:
        return HEAVY_RAIN_OUTDOOR_FACTOR if heavy else RAIN_OUTDOOR_FACTOR
    if key in INDOOR_CATEGORIES:
        return HEAVY_RAIN_INDOOR_FACTOR if heavy else RAIN_INDOOR_FACTOR
    return 1.0
