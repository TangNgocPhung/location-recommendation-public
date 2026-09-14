import logging
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import directions, geofence, photos, poi_detail, reviews
from .ranking_snapshots import record_snapshot
from .config import settings
from .geocoding import parse_location, reverse_geocode
from .ingestion import ingestion_status, persist_events, publish_events
from .ltr import model as ltr_model
from .models import EventBatch, GeofenceRequest, GeoParseRequest, ReviewRequest, SearchRequest
from .features.online import feature_store_status
from .features.serving import profile_category_boost, session_profile
from .graph.recommend import graph_candidate_ids
from .poi_import import data_status
from .ranking import (
    fetch_categories,
    fetch_category_affinity,
    fetch_trending,
    rank_pois,
    rank_pois_detailed,
)


DATABASE_URL = settings.database_url
RATE_LIMIT_PER_MINUTE = settings.rate_limit_per_minute
logger = logging.getLogger("nearby-api")

app = FastAPI(
    title="Nearby POI API",
    version="0.3.0",
    description="Tầng thu thập dữ liệu, định vị và tìm kiếm POI theo không gian.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    # DELETE cần cho /api/v1/geofences/{id}: thiếu nó thì trình duyệt chặn ở
    # bước preflight và nút "bỏ nhắc" hỏng lặng lẽ, chỉ thấy lỗi trong console.
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms", "X-RateLimit-Limit"],
)

rate_windows: dict[str, deque[float]] = defaultdict(deque)
TRUSTED_PROXY_HOPS = settings.trusted_proxy_hops


def client_ip(request: Request) -> str:
    """IP thật của client, có tính tới các proxy tin cậy đứng trước.

    Lấy phần tử thứ `trusted_proxy_hops` ĐẾM TỪ CUỐI của X-Forwarded-For, vì mỗi
    proxy nối thêm IP của peer trực tiếp vào cuối. Không bao giờ lấy phần tử đầu:
    đó là phần client tự gửi và bịa được.
    """
    peer = request.client.host if request.client else "unknown"
    if TRUSTED_PROXY_HOPS <= 0:
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return peer
    # Danh sách ngắn hơn số hop khai báo: cấu hình sai hoặc ai đó gọi thẳng API,
    # không bỏ qua proxy nào cả — lấy phần tử đầu tiên còn tin được là phần tử đầu.
    index = max(0, len(chain) - TRUSTED_PROXY_HOPS)
    return chain[index] if index < len(chain) else chain[0]


@app.middleware("http")
async def gateway_context(request: Request, call_next) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = time.monotonic()
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        try:
            UUID(session_id)
        except ValueError:
            response = JSONResponse(
                status_code=400,
                content={"detail": "X-Session-ID must be a UUID"},
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time-Ms"] = f"{(time.monotonic() - started) * 1000:.2f}"
            return response

    client_key = session_id or client_ip(request)
    now = time.monotonic()
    window = rate_windows[client_key]
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        response = Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
        )
        response.headers["Retry-After"] = "60"
    else:
        window.append(now)
        request.state.request_id = request_id
        request.state.session_id = session_id
        response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{(time.monotonic() - now) * 1000:.2f}"
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_PER_MINUTE)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.get("/health")
def health(response: Response) -> dict[str, Any]:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "service": "nearby-ingestion-api",
        "version": app.version,
        # Lộ ra để giao diện biết có nên hiện nút chỉ đường trong ứng dụng hay
        # rơi về deep-link Google Maps, thay vì bấm xong mới biết là hỏng.
        "routing": bool(settings.osrm_url),
    }


@app.get("/api/pois/nearby")
def nearby_pois(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: int = Query(default=3_000, ge=100, le=50_000),
    q: str | None = Query(default=None, min_length=1, max_length=160),
    category: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, Any]]:
    return rank_pois(lat, lng, radius, q, category, limit)


@app.post("/api/v1/search")
def contextual_search(payload: SearchRequest, request: Request) -> dict[str, Any]:
    parsed = parse_location(
        DATABASE_URL,
        payload.query,
        payload.latitude,
        payload.longitude,
    )
    center_latitude = payload.latitude
    center_longitude = payload.longitude
    if parsed.get("bestMatch"):
        center_latitude = parsed["bestMatch"]["latitude"]
        center_longitude = parsed["bestMatch"]["longitude"]
    subject = parsed["subject"] or None

    # Cá nhân hóa trên ĐƯỜNG TÌM KIẾM, không chỉ trên /recommendations.
    # `SearchRequest` vốn đã nhận `session_id` nhưng trước đây không dùng tới,
    # nên hai tín hiệu `graph` (0.10) và `category_boost` luôn bằng 0 với mọi
    # truy vấn — đúng như ablation đo được (Δ = 0 khi tắt `graph`).
    affinity: dict[str, float] = {}
    graph_ids: set[str] = set()
    if payload.session_id:
        session_key = str(payload.session_id)
        profile = session_profile(session_key)
        affinity = profile_category_boost(profile) or fetch_category_affinity(session_key)
        graph_ids = set(graph_candidate_ids(session_key))

    geo_telemetry: dict[str, Any] = {}
    results, retrieval_backend = rank_pois_detailed(
        center_latitude,
        center_longitude,
        payload.radius,
        subject,
        payload.category,
        payload.limit,
        category_boost=affinity,
        graph_boost=graph_ids,
        ranker=payload.ranker,
        telemetry=geo_telemetry,
    )
    # Gán rank Ở ĐÂY chứ không trong ranking.py: results tại điểm này đã là thứ
    # tự CUỐI CÙNG sau diversify() — mà diversify đảo thứ tự so với điểm số. Gán
    # theo score thì rank không khớp thứ tự người dùng nhìn thấy, và mọi phân
    # tích position bias sau này thành vô nghĩa. Đặt trong rank_pois_detailed
    # cũng sai vì rank_pois() bọc lại nó, làm /pois/nearby và /recommendations
    # lộ thêm trường rank ngoài ý muốn.
    for index, poi in enumerate(results):
        poi["rank"] = index
    # Ảnh chụp feature THẬT ngay lúc này — sau khi có rank cuối cùng hiển thị
    # cho người dùng, cùng category_boost/graph_boost đã dùng để xếp hạng.
    # Lỗi ghi (DB tạm gián đoạn) không được làm hỏng response tìm kiếm.
    try:
        record_snapshot(
            request.state.request_id,
            payload.session_id,
            retrieval_backend,
            results,
            category_boost=affinity,
            graph_boost=graph_ids,
        )
    except psycopg.Error as error:
        # Không được làm hỏng response tìm kiếm, nhưng im lặng hoàn toàn thì
        # một lỗi ghi snapshot (như bug NaN/JSON đã gặp) có thể kéo dài hàng
        # tháng mà không ai biết dataset training đang rỗng.
        logger.warning("Ghi ranking_snapshots thất bại cho request %s: %s", request.state.request_id, error)
    return {
        "requestId": request.state.request_id,
        "query": subject or "",
        "searchCenter": {
            "latitude": center_latitude,
            "longitude": center_longitude,
            "source": "parsed-location" if parsed.get("matched") else "device-location",
        },
        "parsedLocation": parsed,
        # "opensearch" = truy xuất đa kênh; "postgis" = đã rơi về đường dự phòng
        "retrievalBackend": retrieval_backend,
        # Kênh không gian đã lọc bằng gì: "h3" (vành hexagon, đúng sơ đồ),
        # "geo_distance" (tính khoảng cách từng document) hay "postgis" (đã
        # fallback). Cùng lý do với `retrievalBackend`: hexagon nằm sẵn trong
        # DB không chứng minh được nó có tham gia truy xuất hay không.
        "geoFilter": geo_telemetry or None,
        # Bộ xếp hạng ĐÃ CHẠY THẬT, không phải cái được yêu cầu: xin "ltr" mà
        # thiếu file mô hình thì hệ thống rơi về "linear" một cách im lặng, và
        # nếu không lộ ra đây thì mọi số đo sau đó bị gán nhầm nhãn.
        "ranker": (results[0].get("rankerUsed") if results else payload.ranker),
        "results": results,
    }


def is_postgres_uuid(value: str) -> bool:
    """Chốt chặt hơn `geofence.is_uuid` cho hai endpoint đẩy thẳng chuỗi vào cột ``uuid``.

    ``UUID()`` của Python dễ dãi hơn hẳn kiểu ``uuid`` của Postgres: nó nuốt cả
    "urn:uuid:<id>", "uuid:<id>", ngoặc nhọn lệch vế ("}<id>") và gạch nối đặt
    tuỳ tiện. Dấu hai chấm hợp lệ trong path segment nên Starlette đưa nguyên
    chuỗi vào handler; chuỗi đó lọt chốt rồi đi thẳng vào ``WHERE id = %s``,
    psycopg ném DataError giữa chừng và FastAPI trả 500 kèm traceback DB trong
    log — thay vì 400 tiếng Việt như hợp đồng đã hứa.

    Cách siết: so lại chuỗi ĐÃ CHUẨN HOÁ chứ không chỉ hỏi "parse được không".
    Hệ quả có chủ đích: dạng 32 ký tự không gạch nối và "{<id>}" đủ cặp — cả hai
    Postgres đều nhận — từ nay trả 400. Không client nào gửi các dạng đó (mọi id
    API phát ra đều là ``id::text``, tức dạng chuẩn).

    KHÔNG siết thẳng trong `geofence.is_uuid`: hàm đó còn là chốt của
    /api/v1/directions và DELETE /api/v1/geofences, đổi nó là đổi hành vi của
    hai tính năng không nằm trong phạm vi bản sửa này.
    """
    if not geofence.is_uuid(value):
        return False
    return str(UUID(value)) == value.lower()


@app.get("/api/v1/pois/{poi_id}")
def get_poi_detail(
    poi_id: str,
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
) -> Any:
    """Toàn bộ dữ liệu trang chi tiết, chỉ đọc Postgres.

    Không đụng "/api/pois/nearby": tiền tố khác hẳn (`/api/v1/pois` so với
    `/api/pois`), nên `{poi_id}` không bao giờ nuốt mất chuỗi "nearby".

    ``lat``/``lng`` là vị trí người dùng và HOÀN TOÀN tuỳ chọn. Thiếu chúng thì
    ``distanceMeters``/``etaMinutes`` trả ``null`` chứ không trả 0 — không có
    điểm xuất phát thì không có khoảng cách.
    """
    if not is_postgres_uuid(poi_id):
        return JSONResponse(status_code=400, content={"detail": "poi_id phải là UUID"})
    detail = poi_detail.fetch_detail(poi_id, lat, lng)
    if detail is None:
        return JSONResponse(status_code=404, content={"detail": "Không có địa điểm này"})
    return detail


@app.post("/api/v1/pois/{poi_id}/reviews", status_code=201)
def create_review(poi_id: str, payload: ReviewRequest, request: Request) -> Any:
    """Đánh giá 1-5 sao thật (explicit feedback) cho một POI.

    Khác ClientEvent event_type='review' (tín hiệu nhẹ qua /api/v1/events/batch,
    dùng để tính category affinity) — cái này ghi vào `poi_reviews`, hiển thị
    công khai qua `/api/v1/pois/{poi_id}` (`reviewSummary`), và cập nhật ngay
    `pois.rating`/`review_count` với `rating_source='user'`.

    Phiên lấy từ header ``X-Session-ID`` trước, rồi mới tới body — cùng quy ước
    với `/api/v1/geofences`.
    """
    if not is_postgres_uuid(poi_id):
        return JSONResponse(status_code=400, content={"detail": "poi_id phải là UUID"})
    session_id = getattr(request.state, "session_id", None) or (
        str(payload.session_id) if payload.session_id else None
    )
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "Cần X-Session-ID hoặc session_id trong body"},
        )
    result = reviews.submit_review(
        poi_id=poi_id,
        session_id=session_id,
        rating=payload.rating,
        author_name=payload.author_name,
        title=payload.title,
        body=payload.body,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "Không có địa điểm này"})
    return result


@app.get("/api/v1/pois/{poi_id}/reviews/me")
def get_my_review(poi_id: str, request: Request) -> Any:
    """Đánh giá của phiên hiện tại, dùng để điền lại form khi người dùng sửa."""
    if not is_postgres_uuid(poi_id):
        return JSONResponse(status_code=400, content={"detail": "poi_id phải là UUID"})
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "Cần X-Session-ID"})
    return {"review": reviews.get_user_review(poi_id, session_id)}


@app.get("/api/v1/pois/{poi_id}/photos")
def get_poi_photos(
    poi_id: str,
    limit: int = Query(default=photos.MAX_PHOTOS, ge=1, le=photos.MAX_PHOTOS),
) -> Any:
    """Ảnh của một địa điểm. Tách khỏi endpoint chi tiết vì CÓ THỂ gọi mạng.

    Wikimedia bắt giãn nhịp tối thiểu một giây giữa hai request, nên một lần dò
    nguội mất vài giây. Gộp chung vào endpoint chi tiết thì cả trang phải chờ
    ảnh; tách ra thì giao diện gọi song song, trang hiện ngay và ảnh điền vào
    sau.

    Ba trạng thái, không được rút còn hai:
      ``ready``       — có ảnh.
      ``empty``       — ĐÃ hỏi Wikimedia, quanh đây thật sự không có ảnh nào.
      ``unavailable`` — CHƯA hỏi được (tắt cấu hình, hoặc không ra được mạng).
    Gộp ``empty`` với ``unavailable`` là biến một lần rớt mạng thành lời khẳng
    định sai về dữ liệu.
    """
    if not is_postgres_uuid(poi_id):
        return JSONResponse(status_code=400, content={"detail": "poi_id phải là UUID"})

    context = photos.poi_photo_context(poi_id)
    if context is None:
        return JSONResponse(status_code=404, content={"detail": "Không có địa điểm này"})

    cached = photos.cached_photos(poi_id, limit)
    if cached is not None:
        return cached

    if not settings.photos_enabled:
        return {"poiId": poi_id, "status": "unavailable", "fetchedAt": None, "photos": []}

    return photos.fetch_and_store(
        poi_id, context["latitude"], context["longitude"], context["tags"], limit
    )


@app.get("/api/v1/categories")
def list_categories() -> list[dict[str, Any]]:
    return fetch_categories()


@app.get("/api/v1/trending")
def trending(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
    return fetch_trending(limit)


@app.get("/api/v1/recommendations")
def recommendations(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: int = Query(default=5_000, ge=100, le=50_000),
    session_id: UUID | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    # Ưu tiên đọc user profile từ ML Feature Store (online store); nếu chưa
    # materialize thì tính trực tiếp từ Postgres như trước.
    profile = session_profile(str(session_id)) if session_id else None
    affinity = profile_category_boost(profile)
    if not affinity and session_id:
        affinity = fetch_category_affinity(str(session_id))
    graph_ids = set(graph_candidate_ids(str(session_id))) if session_id else set()
    results = rank_pois(
        lat, lng, radius, None, None, limit, category_boost=affinity, graph_boost=graph_ids
    )
    top_category = max(affinity, key=affinity.get) if affinity else None
    for poi in results:
        if poi.get("graphRecommended"):
            poi["reason"] = "Người có hành vi tương tự cũng thích địa điểm này"
        elif top_category and poi["category"] == top_category:
            poi["reason"] = f"Vì bạn hay xem địa điểm {poi['categoryLabel']}"
        elif affinity:
            poi["reason"] = "Gợi ý dựa trên lịch sử tìm kiếm của bạn"
        else:
            poi["reason"] = "Đang được nhiều người quan tâm gần đây"
    return {
        "personalized": bool(affinity) or bool(graph_ids),
        "graphRecommendations": len(graph_ids),
        "profileSource": "feature-store" if profile else ("postgres" if affinity else "none"),
        "preferredCategories": sorted(affinity, key=affinity.get, reverse=True),
        "results": results,
    }


@app.post("/api/v1/geocode/parse")
def geocode_parse(payload: GeoParseRequest) -> dict[str, Any]:
    return parse_location(DATABASE_URL, payload.text, payload.latitude, payload.longitude)


@app.get("/api/v1/geocode/reverse")
def geocode_reverse(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
) -> dict[str, Any]:
    return reverse_geocode(DATABASE_URL, lat, lng)


@app.post("/api/v1/events/batch", status_code=202)
def ingest_events(batch: EventBatch) -> dict[str, Any]:
    accepted_ids = persist_events(batch.events)
    accepted_set = set(accepted_ids)
    new_events = [event for event in batch.events if str(event.id) in accepted_set]
    queued_count, stream_available = publish_events(new_events) if new_events else (0, True)
    return {
        "accepted": len(accepted_ids),
        "eventIds": accepted_ids,
        "queued": queued_count,
        "delivery": "redis-stream" if stream_available else "durable-postgres-fallback",
    }


@app.get("/api/v1/ingestion/status")
def get_ingestion_status(
    session_id: UUID | None = Query(default=None),
) -> dict[str, Any]:
    return ingestion_status(str(session_id) if session_id else None)


@app.get("/api/v1/data/status")
def get_data_status() -> dict[str, Any]:
    return data_status(DATABASE_URL)


@app.get("/api/v1/features/status")
def get_feature_status() -> dict[str, Any]:
    """Độ tươi/độ phủ của ML Feature Store (online store)."""
    return feature_store_status()


@app.get("/api/v1/directions")
def get_directions(
    from_lat: float = Query(ge=-90, le=90),
    from_lng: float = Query(ge=-180, le=180),
    to_poi_id: str | None = Query(default=None, min_length=1, max_length=64),
    to_lat: float | None = Query(default=None, ge=-90, le=90),
    to_lng: float | None = Query(default=None, ge=-180, le=180),
    to_name: str = Query(default="Điểm đến", min_length=1, max_length=200),
    mode: str = Query(default=directions.DEFAULT_MODE, max_length=16),
) -> dict[str, Any]:
    """Tuyến đường thật từ vị trí người dùng tới một POI.

    ``mode`` là "car" | "motorbike" | "foot" (xem ``directions.MODES``) — mỗi
    hồ sơ một đồ thị OSRM riêng. Khi đồ thị của hồ sơ được yêu cầu chưa dựng,
    response tự khai báo ``route.approximate = true`` để tầng gọi biết tuyến
    đang tính bằng đồ thị mượn tạm.

    POI thật nhận bằng ``to_poi_id`` và luôn lấy toạ độ đích từ database. Cặp
    ``to_lat``/``to_lng`` chỉ dành cho các địa điểm mẫu của giao diện, vốn chưa
    có UUID trong database nhưng vẫn phải chỉ đường được ngay trong ứng dụng.
    """
    if mode not in directions.MODES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"mode phải là một trong {sorted(directions.MODES)}"},
        )

    destination_id: str
    if to_poi_id is not None:
        if not geofence.is_uuid(to_poi_id):
            return JSONResponse(status_code=400, content={"detail": "to_poi_id phải là UUID"})
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT name, ST_Y(location::geometry), ST_X(location::geometry)
                    FROM pois WHERE id = %s
                    """,
                    (to_poi_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return JSONResponse(status_code=404, content={"detail": "Không có POI này"})
        name, destination_lat, destination_lng = row
        destination_id = to_poi_id
    elif to_lat is not None and to_lng is not None:
        name = to_name.strip()
        destination_lat, destination_lng = to_lat, to_lng
        destination_id = "sample-coordinate"
    else:
        return JSONResponse(
            status_code=400,
            content={"detail": "Cần to_poi_id hoặc đầy đủ to_lat và to_lng"},
        )

    result = directions.route(
        from_lat,
        from_lng,
        float(destination_lat),
        float(destination_lng),
        mode,
    )
    if result is None:
        # 200 kèm route rỗng chứ không phải 5xx: "chưa dựng OSRM" và "OSRM chết"
        # đều là trạng thái BÌNH THƯỜNG của hệ thống này, và giao diện cần phân
        # biệt chúng với một lỗi thật để còn rơi về deep-link.
        osrm_url = getattr(settings, directions.MODES[mode]["osrm_url_attr"])
        return {
            "poiId": destination_id,
            "poiName": name,
            "destination": {
                "latitude": float(destination_lat),
                "longitude": float(destination_lng),
            },
            "route": None,
            "reason": "osrm-unavailable" if not osrm_url else "no-route",
        }

    return {
        "poiId": destination_id,
        "poiName": name,
        "origin": {"latitude": from_lat, "longitude": from_lng},
        "destination": {
            "latitude": float(destination_lat),
            "longitude": float(destination_lng),
        },
        "route": result,
    }


@app.post("/api/v1/geofences", status_code=201)
def create_geofence(payload: GeofenceRequest, request: Request) -> dict[str, Any]:
    """Đăng ký nhắc khi tới gần một POI ("Proximity Notification Service").

    Phiên lấy từ header ``X-Session-ID`` trước, rồi mới tới body: header đã được
    middleware kiểm là UUID hợp lệ, còn body thì client tự khai.
    """
    session_id = getattr(request.state, "session_id", None) or (
        str(payload.session_id) if payload.session_id else None
    )
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "Cần X-Session-ID hoặc session_id trong body"},
        )
    created = geofence.subscribe(
        session_id=session_id,
        poi_id=str(payload.poi_id),
        radius_meters=payload.radius_meters,
        label=payload.label,
        notify_only_when_open=payload.notify_only_when_open,
        cooldown_minutes=payload.cooldown_minutes,
    )
    if created is None:
        return JSONResponse(status_code=404, content={"detail": "Không có POI này"})
    return created


@app.get("/api/v1/geofences")
def list_geofences(request: Request) -> dict[str, Any]:
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        return {"subscriptions": [], "reason": "no-session"}
    return {"subscriptions": geofence.list_subscriptions(session_id)}


@app.delete("/api/v1/geofences/{subscription_id}")
def delete_geofence(subscription_id: str, request: Request) -> dict[str, Any]:
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "Cần X-Session-ID"})
    if not geofence.is_uuid(subscription_id):
        return JSONResponse(status_code=400, content={"detail": "subscription_id phải là UUID"})
    removed = geofence.unsubscribe(session_id, subscription_id)
    if not removed:
        return JSONResponse(status_code=404, content={"detail": "Không có vùng nhắc này"})
    return {"deleted": subscription_id}


@app.get("/api/v1/notifications/stream")
def notification_stream(request: Request) -> Response:
    """Server-Sent Events: đẩy thông báo tới gần xuống trình duyệt.

    Chọn SSE thay vì Web Push vì SSE không cần VAPID key, không cần máy chủ đẩy
    của bên thứ ba, và chỉ cần một endpoint. Đánh đổi: chỉ chạy khi tab còn mở —
    với phạm vi một đồ án thì đó là đánh đổi đúng, và phải nói rõ trong báo cáo
    thay vì để hội đồng tự phát hiện.

    Mỗi kết nối tự đóng sau ``NOTIFICATION_STREAM_TTL_SECONDS``; trình duyệt tự
    kết nối lại. Không có trần này thì mỗi tab bỏ quên giữ một luồng vĩnh viễn
    trong threadpool của FastAPI.
    """
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "Cần X-Session-ID"})
    return StreamingResponse(
        geofence.stream_notifications(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Bắt buộc khi đứng sau nginx: proxy_buffering mặc định gom kết quả
            # lại rồi mới trả, nên SSE không bao giờ tới nơi đúng lúc.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/v1/ltr/status")
def get_ltr_status() -> dict[str, Any]:
    """Mô hình LambdaMART đang nạp được hay không, và huấn luyện trên bao nhiêu.

    Cần lộ ra ngoài vì `?ranker=ltr` rơi về tuyến tính một cách IM LẶNG khi
    thiếu mô hình: không có endpoint này thì không cách nào biết một phép đo
    "LTR" có thật sự chạy LTR hay không.
    """
    return ltr_model.info()
