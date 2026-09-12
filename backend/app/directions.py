"""Chỉ đường thật bằng OSRM (lộ trình B16).

Trước bước này, "Chỉ đường" chỉ là ``window.open('https://google.com/maps/dir/…')``
— tức đá người dùng sang Google Maps, và chức năng chỉ đường nằm **ngoài** hệ
thống. Sơ đồ kiến trúc có ô "Chỉ đường" ở tầng 4; một deep-link không lấp được
ô đó.

Đồng thời ``spatio_temporal.eta_minutes`` ước lượng thời gian bằng **đường chim
bay chia vận tốc cố định**, nên luôn lạc quan: đường phố TP.HCM có hệ số vòng
vèo khoảng 1,3–1,4 lần so với đường thẳng. OSRM thay con số đó bằng thời gian
trên tuyến đường có thật.

Thiết kế:

- **Tự dựng OSRM, không gọi máy chủ demo công cộng.** Máy chủ demo của dự án
  OSRM cấm dùng cho ứng dụng thật và có giới hạn tần suất; một đồ án phụ thuộc
  vào nó sẽ hỏng đúng lúc bảo vệ nếu mạng chập hoặc bị chặn tần suất.
- **Cache Redis theo cặp toạ độ đã làm tròn.** Làm tròn 4 chữ số thập phân
  (~11 m): người dùng nhích vài mét không sinh thêm một lần tính tuyến.
- **Lỗi thì trả None, không ném.** OSRM chết thì nút chỉ đường mất tác dụng,
  chứ không được làm hỏng cả trang kết quả.

Lưu ý phải ghi trong báo cáo: hồ sơ định tuyến mặc định của OSRM là **ô tô**.
Xe máy ở TP.HCM đi được nhiều đường mà ô tô không đi được, nên thời gian OSRM
trả về là cận trên cho xe máy. Muốn đúng hơn thì cần hồ sơ Lua riêng — nằm
ngoài phạm vi bước này, nhưng phải nói ra thay vì để người đọc tưởng con số là
thời gian xe máy.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import redis

from .config import settings

logger = logging.getLogger("nearby-directions")

CACHE_PREFIX = "nearby:route"
CACHE_TTL_SECONDS = 6 * 3600
REQUEST_TIMEOUT_SECONDS = 4.0

# Làm tròn toạ độ khi dựng khóa cache. 4 chữ số ~ 11 m: đủ mịn để tuyến không
# lệch thấy được, đủ thô để người dùng đứng yên không sinh khóa mới mỗi lần GPS
# nhiễu vài mét.
CACHE_PRECISION = 4

# OSRM dựng sẵn ba hồ sơ Lua (car/bicycle/foot), nhưng MỘT osrm-routed chỉ
# phục vụ ĐÚNG MỘT đồ thị mỗi lần chạy — không "đổi profile lúc gọi API" được
# trên cùng một file .osrm. Nên mỗi hồ sơ là MỘT container + MỘT base URL
# riêng (xem docker-compose.yml: service "osrm" cho car, "osrm-foot" cho foot).
#
# KHÔNG có hồ sơ xe máy thật: ảnh chính thức chỉ có car/bicycle/foot, và viết
# hồ sơ Lua riêng cho xe máy (tốc độ, access, turn theo đúng luật xe máy VN)
# là việc lớn, nằm ngoài phạm vi một người làm đồ án (Phase 12.7). "motorbike"
# vì vậy CỐ Ý dùng lại đồ thị "car" — kết quả là XẤP XỈ, và mọi response phải
# tự khai báo `"approximate": true` để tầng gọi (API, giao diện) không được
# phép im lặng coi nó là tuyến xe máy thật.
MODES: dict[str, dict[str, Any]] = {
    "car": {"osrm_url_attr": "osrm_url", "api_profile": "driving", "approximate": False},
    "motorbike": {"osrm_url_attr": "osrm_url", "api_profile": "driving", "approximate": True},
    "foot": {"osrm_url_attr": "osrm_foot_url", "api_profile": "foot", "approximate": False},
}
DEFAULT_MODE = "car"

# Chỉ giữ lại những bước rẽ có ý nghĩa. OSRM trả cả "depart"/"arrive" và nhiều
# bước dài 0 m ở giao lộ — hiển thị hết thì danh sách dài gấp ba mà không thêm
# thông tin nào.
MIN_STEP_DISTANCE_METERS = 15.0


def _cache_key(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, mode: str
) -> str:
    def r(value: float) -> str:
        return f"{round(float(value), CACHE_PRECISION):.{CACHE_PRECISION}f}"

    return f"{CACHE_PREFIX}:{mode}:{r(from_lat)},{r(from_lng)}:{r(to_lat)},{r(to_lng)}"


def _get_redis() -> Any | None:
    try:
        return redis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=1.5
        )
    except (redis.RedisError, OSError, ValueError):
        return None


def _maneuver_text(step: dict[str, Any]) -> str:
    """Đổi mô tả máy của OSRM thành một câu tiếng Việt đọc được.

    OSRM trả `maneuver.type` + `maneuver.modifier` chứ không trả câu chữ — phần
    sinh câu nằm ở thư viện hiển thị của bên gọi. Bảng dưới đây đủ cho các loại
    xuất hiện trong dữ liệu đô thị; loại lạ rơi về mô tả chung thay vì hiện ra
    một chuỗi tiếng Anh giữa giao diện tiếng Việt.
    """
    maneuver = step.get("maneuver") or {}
    kind = maneuver.get("type") or ""
    modifier = maneuver.get("modifier") or ""
    road = (step.get("name") or "").strip()

    directions = {
        "left": "rẽ trái",
        "right": "rẽ phải",
        "sharp left": "rẽ gắt sang trái",
        "sharp right": "rẽ gắt sang phải",
        "slight left": "chếch sang trái",
        "slight right": "chếch sang phải",
        "straight": "đi thẳng",
        "uturn": "quay đầu",
    }
    turn = directions.get(modifier, "")

    if kind == "depart":
        base = "Bắt đầu đi"
    elif kind == "arrive":
        base = "Tới nơi"
    elif kind == "roundabout" or kind == "rotary":
        exit_number = maneuver.get("exit")
        base = f"Vào vòng xoay, ra lối {exit_number}" if exit_number else "Vào vòng xoay"
    elif kind == "merge":
        base = f"Nhập làn {turn}".strip()
    elif kind == "fork":
        base = f"Tại ngã ba {turn}".strip()
    elif kind in ("new name", "continue"):
        base = "Đi tiếp"
    elif turn:
        base = turn.capitalize()
    else:
        base = "Đi tiếp"

    return f"{base} vào {road}" if road and kind != "arrive" else base


def _shape_response(payload: dict[str, Any], mode: str, approximate: bool) -> dict[str, Any] | None:
    routes = payload.get("routes") or []
    if not routes:
        return None
    route = routes[0]
    geometry = route.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return None

    steps: list[dict[str, Any]] = []
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            distance = float(step.get("distance") or 0.0)
            kind = ((step.get("maneuver") or {}).get("type")) or ""
            if distance < MIN_STEP_DISTANCE_METERS and kind not in ("depart", "arrive"):
                continue
            steps.append(
                {
                    "text": _maneuver_text(step),
                    "distanceMeters": round(distance, 1),
                    "durationSeconds": round(float(step.get("duration") or 0.0), 1),
                    "name": (step.get("name") or "").strip() or None,
                }
            )

    return {
        "geometry": geometry,
        "distanceMeters": round(float(route.get("distance") or 0.0), 1),
        "durationSeconds": round(float(route.get("duration") or 0.0), 1),
        "durationMinutes": max(1, round(float(route.get("duration") or 0.0) / 60.0)),
        "steps": steps,
        "engine": "osrm",
        "mode": mode,
        # True cho "motorbike": tuyến thật sự tính bằng đồ thị "car" (không có
        # hồ sơ xe máy thật — xem MODES ở đầu file). Tầng gọi PHẢI hiển thị rõ
        # điều này, không được trình bày như tuyến xe máy thật.
        "approximate": approximate,
    }


def _fetch_route(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, mode: str
) -> dict[str, Any] | None:
    config = MODES.get(mode)
    if config is None:
        return None
    base_url = getattr(settings, config["osrm_url_attr"])
    if not base_url:
        return None
    # OSRM nhận toạ độ theo thứ tự KINH ĐỘ TRƯỚC. Đảo thứ tự không gây lỗi HTTP,
    # chỉ cho ra một tuyến đường ở giữa biển — đúng kiểu hỏng im lặng.
    coords = f"{from_lng:.6f},{from_lat:.6f};{to_lng:.6f},{to_lat:.6f}"
    query = urllib.parse.urlencode(
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "alternatives": "false",
        }
    )
    url = f"{base_url.rstrip('/')}/route/v1/{config['api_profile']}/{coords}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Không tính được tuyến đường qua OSRM (%s): %s", mode, error)
        return None

    if payload.get("code") != "Ok":
        # "NoRoute" là câu trả lời hợp lệ (hai điểm không nối được bằng đường bộ),
        # không phải sự cố — ghi ở mức debug để log không đầy cảnh báo giả.
        logger.debug("OSRM trả mã %s (%s)", payload.get("code"), mode)
        return None
    return _shape_response(payload, mode, config["approximate"])


def route(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    mode: str = DEFAULT_MODE,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Tuyến đường thật giữa hai điểm, ưu tiên cache Redis.

    ``mode`` là "car" | "motorbike" | "foot" — xem ``MODES``. Trả ``None`` khi
    không tính được (mode lạ, chưa cấu hình URL, hoặc OSRM không tìm được
    đường) — gọi bên phải coi đó là "chưa có tuyến" chứ không phải "không có
    đường đi".
    """
    if mode not in MODES:
        return None
    key = _cache_key(from_lat, from_lng, to_lat, to_lng, mode)
    client = client if client is not None else _get_redis()

    if client is not None:
        try:
            cached = client.get(key)
            if cached:
                data = json.loads(cached)
                data["cached"] = True
                return data
        except (redis.RedisError, OSError, ValueError, json.JSONDecodeError):
            pass

    result = _fetch_route(from_lat, from_lng, to_lat, to_lng, mode)
    if result is None:
        return None

    if client is not None:
        try:
            client.setex(key, CACHE_TTL_SECONDS, json.dumps(result, ensure_ascii=False))
        except (redis.RedisError, OSError, TypeError):
            pass
    result["cached"] = False
    return result


def available(mode: str = DEFAULT_MODE) -> bool:
    """OSRM của ``mode`` có đang phục vụ không. Dùng cho /health và để giao
    diện ẩn từng nút phương tiện riêng — "car" sống không có nghĩa "foot"
    cũng sống, vì đó là hai container khác nhau."""
    config = MODES.get(mode)
    if config is None:
        return False
    base_url = getattr(settings, config["osrm_url_attr"])
    if not base_url:
        return False
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/route/v1/{config['api_profile']}/"
            "106.7009,10.7757;106.7018,10.7784?overview=false",
            timeout=2.0,
        ) as response:
            return json.load(response).get("code") == "Ok"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
