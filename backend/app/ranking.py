"""Tầng 3 — ranking engine đa tín hiệu.

Luồng: "Multi-Channel Candidate Retrieval" → "Spatio-Temporal Enricher" →
"LTR & Neural Re-ranking" (hiện là re-rank tuyến tính) → "Diversity & Business
Rules".

Bước truy xuất ứng viên (`retrieve_candidates`) ưu tiên OpenSearch đa kênh —
BM25 không dấu + fuzzy, geo, vector k-NN và trending Redis gộp bằng Reciprocal
Rank Fusion (xem gói `app.search`). Khi OpenSearch chưa cấu hình hoặc lỗi, hệ
thống tự rơi về truy xuất PostGIS thuần (`fetch_candidates`: pg_trgm + khoảng
cách không gian) nên app luôn chạy được. Tín hiệu thời gian thực trending đọc
từ Redis (`nearby:trending:pois`, do `stream_worker.py` cập nhật). PostGIS luôn
là nguồn dữ liệu chuẩn cho mọi thuộc tính hiển thị và khoảng cách.
"""

import math
from typing import Any

import psycopg
import redis
from psycopg.rows import dict_row

from .config import settings
from .features.serving import attach_region_ctr
from . import geo_cache
from .ltr import model as ltr_model
from .opening_hours import is_open_now
from .search.retrieval import multi_channel_candidates
from .spatio_temporal import enrich_candidates

DATABASE_URL = settings.database_url
REDIS_URL = settings.redis_url
TRENDING_POIS_KEY = "nearby:trending:pois"
TRENDING_QUERIES_KEY = "nearby:trending:queries"

# Khoảng cách (m) mà tín hiệu không gian giảm còn ~37% (1/e) — dùng cho decay
# hàm mũ thay vì công thức tuyến tính cũ.
SPATIAL_DECAY_METERS = 1_500.0

DEFAULT_WEIGHTS: dict[str, float] = {
    "text": 0.26,
    "spatial": 0.24,
    "rating": 0.12,
    "popularity": 0.08,
    "trending": 0.08,
    "recency": 0.12,  # popularity theo cửa sổ 15p/1h/24h (Spatio-Temporal Enricher)
    "context": 0.10,  # giờ mở + phù hợp thời điểm
    "graph": 0.10,  # POI được Knowledge Graph gợi ý ("người tương tự cũng thích")
    "ctr": 0.08,  # CTR lịch sử theo vùng, đọc từ ML Feature Store
}


def enrich_poi_response(poi: dict[str, Any]) -> dict[str, Any]:
    poi["openNow"] = is_open_now(poi.get("openingHours"), poi.get("timezone", "UTC"))
    poi.pop("cachedOpenNow", None)
    return poi


def fetch_candidates(
    latitude: float,
    longitude: float,
    radius: int,
    query_text: str | None,
    category: str | None,
    limit_candidates: int,
) -> list[dict[str, Any]]:
    query = """
        WITH candidates AS (
            SELECT
                id::text AS id, name, description, category, category_label,
                address, opening_hours, timezone, open_now, price_level,
                amenities, tags, brand, district, city, country_code,
                source, source_id, canonical_id, h3_r7, h3_r8, h3_r9,
                embedding_model, updated_at, sponsored_until,
                ST_Y(location::geometry) AS latitude,
                ST_X(location::geometry) AS longitude,
                rating::float8 AS rating, review_count, popularity_score,
                rating_source,
                ST_Distance(
                    location,
                    ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography
                ) AS distance_meters,
                CASE WHEN CAST(%(query_text)s AS text) IS NULL THEN 1.0 ELSE
                    GREATEST(
                        similarity(name, %(query_text)s),
                        similarity(description, %(query_text)s),
                        similarity(category_label, %(query_text)s)
                    )
                END AS text_score
            FROM pois
            WHERE ST_DWithin(
                location,
                ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography,
                %(radius)s
            )
            -- Lọc theo category_label (nhãn hiển thị), không phải category (mã
            -- OSM chi tiết): sau khi nhập OSM thật, nhiều mã khác nhau
            -- (convenience/clothes/supermarket/electronics/shopping_mall...)
            -- cùng chung một nhãn ("Mua sắm"), và người dùng chọn theo nhãn
            -- trên chip lọc chứ không phân biệt được các mã con.
            AND (CAST(%(category)s AS text) IS NULL OR category_label = %(category)s)
            AND (
                CAST(%(query_text)s AS text) IS NULL
                OR name ILIKE '%%' || %(query_text)s || '%%'
                OR description ILIKE '%%' || %(query_text)s || '%%'
                OR category_label ILIKE '%%' || %(query_text)s || '%%'
                OR similarity(name, %(query_text)s) > 0.15
                OR similarity(category_label, %(query_text)s) > 0.15
            )
        )
        SELECT
            id, name, description, category,
            category_label AS "categoryLabel", address, latitude, longitude,
            rating, review_count AS "reviewCount",
            -- Nguon cua diem: 'seed' la du lieu bia chi de demo, 'google' la
            -- danh gia that cua ben thu ba, 'user' la nguoi dung chinh ung
            -- dung nay cham. Lo ra API de giao dien ghi ro nguon va de bao
            -- cao khong tron ba loai voi nhau.
            rating_source AS "ratingSource",
            popularity_score AS "popularityScore",
            opening_hours AS "openingHours", timezone,
            open_now AS "cachedOpenNow", price_level AS "priceLevel",
            (sponsored_until IS NOT NULL AND sponsored_until > NOW()) AS sponsored,
            amenities, tags, brand, district, city,
            country_code AS "countryCode", source, source_id AS "sourceId",
            canonical_id::text AS "canonicalId",
            jsonb_build_object('r7', h3_r7, 'r8', h3_r8, 'r9', h3_r9) AS "h3Cells",
            embedding_model AS "embeddingModel", updated_at AS "updatedAt",
            distance_meters AS "distanceMeters",
            text_score AS "textScore"
        FROM candidates
        ORDER BY distance_meters ASC
        LIMIT %(limit_candidates)s
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "radius": radius,
        "query_text": query_text.strip() if query_text else None,
        "category": category.strip() if category else None,
        "limit_candidates": limit_candidates,
    }
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [enrich_poi_response(dict(row)) for row in cursor.fetchall()]


def apply_trending_boost(
    candidates: list[dict[str, Any]],
    latitude: float | None = None,
    longitude: float | None = None,
) -> list[dict[str, Any]]:
    """Gắn ``trendingScore``, ưu tiên bảng trending của KHU VỰC quanh tâm tìm kiếm.

    Trước đây chỉ có một bảng toàn cục, nên một quán đang hot ở Quận 1 được cộng
    điểm cho cả người đang tìm ở Thủ Đức — "trending" mà không có địa điểm thì
    trên một thành phố 9 triệu dân gần như là vô nghĩa.

    Rơi về bảng toàn cục khi ô quanh tâm chưa có dữ liệu, và ghi lại đã dùng
    nguồn nào trong ``trendingScope``: nếu không, một con số 0 vì khu vực vắng
    và một con số 0 vì Redis chết trông giống hệt nhau.
    """
    if not candidates:
        return candidates

    client = geo_cache.get_client()
    local: dict[str, float] = {}
    if client is not None and latitude is not None and longitude is not None:
        local = geo_cache.trending_pois_near(latitude, longitude, client=client)
    global_scores = geo_cache.trending_pois(client=client) if client is not None else {}

    scope = "hex" if local else ("global" if global_scores else "empty")
    for candidate in candidates:
        poi_id = candidate["id"]
        weight = local.get(poi_id) if local else None
        if weight is None:
            weight = global_scores.get(poi_id, 0.0)
        # Squash để một POI cực trending không nuốt hết trọng số các tín hiệu khác.
        candidate["trendingScore"] = weight / (weight + 10.0)
        candidate["trendingScope"] = scope
    return candidates


def apply_crowd_signal(
    candidates: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    radius: int,
) -> list[dict[str, Any]]:
    """Đếm phiên đang hoạt động quanh mỗi POI, qua GEOSEARCH (lộ trình B6c).

    ``nearby:active-locations`` được GEOADD từ đầu nhưng chưa lệnh nào đọc. Một
    lệnh GEOSEARCH lấy hết toạ độ phiên trong bán kính tìm kiếm, rồi đếm quanh
    từng ứng viên trong Python — gọi GEOSEARCH cho mỗi ứng viên thì một truy vấn
    300 ứng viên thành 300 vòng đi-về.

    CHƯA đưa vào công thức xếp hạng, và đó là chủ ý: với một người dùng lúc
    demo thì con số này luôn là 0 hoặc 1, nên một trọng số gắn vào nó sẽ là
    trọng số của nhiễu. Trường được phơi ra để quan sát và để báo cáo nói được
    rằng dữ liệu GEOADD đã có người đọc.
    """
    if not candidates:
        return candidates
    points = geo_cache.active_session_points(latitude, longitude, radius)
    for candidate in candidates:
        lat, lon = candidate.get("latitude"), candidate.get("longitude")
        if lat is None or lon is None:
            candidate["liveNearbyUsers"] = 0
            continue
        candidate["liveNearbyUsers"] = geo_cache.count_near(points, float(lat), float(lon))
    return candidates


def _is_text_relevant(candidate: dict[str, Any]) -> bool:
    """Ứng viên có ÍT NHẤT một tín hiệu liên quan văn bản (BM25 hoặc vector).

    ``textScore`` mặc định 1.0 khi KHÔNG có query text (mọi ứng viên "khớp"
    như nhau — xem ``search/enrichment.hydrate_candidates``), nên hàm này chỉ
    có ý nghĩa khi gọi cùng ``has_query_text=True``.
    """
    return candidate.get("textScore", 1.0) > 0.0 or candidate.get("vectorScore") is not None


def _relevance_sort_key(has_query_text: bool):
    """Khoá sắp xếp: khi có query text, ứng viên KHÔNG có tín hiệu liên quan
    văn bản nào (chỉ lọt vào nhờ geo/trending) luôn đứng SAU mọi ứng viên có
    tín hiệu, bất kể điểm cuối cùng cao thấp ra sao.

    Đo được thật: truy vấn "cà phê" (bán kính 3km, limit 50) vẫn xếp "Phở Nhà
    Mình" (không BM25, không vector, chỉ gần 174m + đang trending) ở HẠNG 1 dù
    `search/retrieval._gate_by_text_relevance` đã ưu tiên đúng thứ tự candidate
    lúc truy xuất — vì bước này CHỈ quyết định ai LỌT VÀO candidate pool, còn
    thứ tự HIỂN THỊ CUỐI CÙNG do `rerank` tính lại từ đầu và ghi đè hoàn toàn.
    Phải gate lại đúng ở đây, nơi thứ tự thật sự được quyết định.
    """

    def key(item: dict[str, Any]) -> tuple[int, float, float]:
        demoted = 1 if has_query_text and not _is_text_relevant(item) else 0
        return (demoted, -item["score"], item["distanceMeters"])

    return key


def rerank(
    candidates: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    category_boost: dict[str, float] | None = None,
    graph_boost: set[str] | None = None,
    ranker: str = "linear",
    has_query_text: bool = False,
) -> list[dict[str, Any]]:
    """Chấm điểm và sắp xếp ứng viên.

    ``ranker="ltr"`` dùng mô hình LambdaMART nếu nạp được; không nạp được thì
    im lặng rơi về công thức tuyến tính — đường tìm kiếm không bao giờ được
    hỏng chỉ vì thiếu file mô hình. Trường ``rankerUsed`` trên mỗi ứng viên
    cho biết thực tế đường nào đã chạy.
    """
    weights = weights or DEFAULT_WEIGHTS
    category_boost = category_boost or {}
    graph_boost = graph_boost or set()
    sort_key = _relevance_sort_key(has_query_text)

    if ranker == "ltr" and candidates:
        # Đặc trưng lấy từ chính dict candidate này, nên LTR phải chạy SAU
        # enrich_candidates/attach_region_ctr giống hệt công thức tuyến tính.
        ltr_scores = ltr_model.score(
            candidates, category_boost=category_boost, graph_boost=graph_boost
        )
        if ltr_scores is not None:
            for candidate, value in zip(candidates, ltr_scores):
                candidate["graphRecommended"] = candidate["id"] in graph_boost
                # Điểm LambdaMART không giới hạn và KHÔNG cùng thang với điểm
                # tuyến tính [0,1]; chỉ so sánh được trong cùng truy vấn.
                candidate["score"] = round(float(value), 6)
                candidate["rankerUsed"] = "ltr"
            candidates.sort(key=sort_key)
            return candidates

    for candidate in candidates:
        spatial_score = math.exp(-candidate["distanceMeters"] / SPATIAL_DECAY_METERS)
        in_graph = candidate["id"] in graph_boost
        rating = candidate.get("rating")

        # (trọng số, giá trị) — giá trị None nghĩa là KHÔNG CÓ DỮ LIỆU, khác
        # hẳn giá trị 0. Xem `signal_values` để biết vì sao phân biệt này quan
        # trọng: `rating` NULL ở 99% POI thật, ép về 0 thì mọi POI thật đều bị
        # phạt như thể bị chấm 0/5.
        terms: list[tuple[float, float | None]] = [
            (weights["text"], candidate.get("textScore", 1.0)),
            (weights["spatial"], spatial_score),
            (weights["rating"], None if rating is None else float(rating) / 5.0),
            (weights["popularity"], candidate.get("popularityScore")),
            (weights["trending"], candidate.get("trendingScore", 0.0)),
            (weights.get("recency", 0.0), candidate.get("recencyScore", 0.0)),
            (weights.get("context", 0.0), candidate.get("contextScore", 0.0)),
            (weights.get("graph", 0.0), 1.0 if in_graph else 0.0),
            (weights.get("ctr", 0.0), candidate.get("regionCtr", 0.0)),
        ]
        # Chuẩn hóa theo tổng trọng số THỰC SỰ áp dụng được. Hai tác dụng:
        # điểm của các ứng viên thiếu dữ liệu khác nhau vẫn so sánh được với
        # nhau, và điểm rơi vào [0,1] thay vì [0,1.18] như khi cộng thẳng.
        applied = sum(w for w, value in terms if value is not None and w)
        raw = sum(w * float(value) for w, value in terms if value is not None and w)
        score = (raw / applied if applied else 0.0) + category_boost.get(
            candidate["category"], 0.0
        )
        candidate["graphRecommended"] = in_graph
        candidate["score"] = round(score, 6)
        candidate["rankerUsed"] = "linear"
    candidates.sort(key=sort_key)
    return candidates


def diversify(
    results: list[dict[str, Any]], max_run: int = 2, max_per_brand: int = 2
) -> list[dict[str, Any]]:
    """Không quá `max_run` kết quả liên tiếp cùng category và không quá
    `max_per_brand` địa điểm cùng thương hiệu, giữ thứ tự điểm số nhiều nhất có thể.

    Luật thương hiệu giải quyết một lỗi rất dễ bị bắt khi demo: tìm "cà phê" ở
    TP.HCM hoàn toàn có thể ra 6 cửa hàng Highlands trong top 10. Địa điểm vượt
    hạn ngạch bị ĐẨY XUỐNG CUỐI chứ không loại hẳn — với bán kính nhỏ, loại hẳn
    sẽ làm hụt kết quả và đó là cái hại lớn hơn.
    """
    if len(results) <= max_run:
        return results

    def brand_of(item: dict[str, Any]) -> str | None:
        brand = (item.get("brand") or "").strip().lower()
        return brand or None  # brand rỗng không phải một thương hiệu

    within_quota: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    brand_counts: dict[str, int] = {}
    for item in results:
        brand = brand_of(item)
        if brand is None:
            within_quota.append(item)
            continue
        brand_counts[brand] = brand_counts.get(brand, 0) + 1
        if brand_counts[brand] <= max_per_brand:
            within_quota.append(item)
        else:
            overflow.append(item)

    ordered: list[dict[str, Any]] = []
    remaining = list(within_quota)
    while remaining:
        run_category = ordered[-1]["category"] if ordered else None
        run_length = 0
        for item in reversed(ordered):
            if item["category"] != run_category:
                break
            run_length += 1
        if run_length >= max_run:
            swap_index = next(
                (i for i, item in enumerate(remaining) if item["category"] != run_category),
                0,
            )
        else:
            swap_index = 0
        ordered.append(remaining.pop(swap_index))
    return ordered + overflow


def ensure_price_diversity(
    results: list[dict[str, Any]], k: int = 10, minimum_affordable: int = 2
) -> list[dict[str, Any]]:
    """Bảo đảm top `k` có ít nhất `minimum_affordable` địa điểm giá phải chăng.

    `priceLevel` 0 nghĩa là CHƯA RÕ, không phải rẻ — phải bỏ qua, nếu không thì
    93% POI thiếu dữ liệu giá sẽ được tính là "rẻ" và luật này thành vô nghĩa.
    """
    top = results[:k]
    affordable = [item for item in top if 1 <= (item.get("priceLevel") or 0) <= 2]
    if len(affordable) >= minimum_affordable:
        return results

    ordered = list(results)
    needed = minimum_affordable - len(affordable)
    for index in range(k, len(ordered)):
        if needed <= 0:
            break
        if 1 <= (ordered[index].get("priceLevel") or 0) <= 2:
            # Đổi chỗ với địa điểm ĐẮT NHẤT trong top, không phải với địa điểm
            # cuối top: mục tiêu là mở rộng dải giá, không phải hạ chất lượng.
            victim = max(
                range(min(k, len(ordered))),
                key=lambda i: ordered[i].get("priceLevel") or 0,
            )
            ordered[victim], ordered[index] = ordered[index], ordered[victim]
            needed -= 1
    return ordered


# Ba vành đai khoảng cách. Mốc 1500 m dùng lại SPATIAL_DECAY_METERS để nhất quán
# với hàm suy giảm không gian trong công thức xếp hạng.
def _distance_band(meters: float) -> int:
    if meters < 500:
        return 0
    if meters <= SPATIAL_DECAY_METERS:
        return 1
    return 2


def ensure_distance_diversity(results: list[dict[str, Any]], k: int = 10) -> list[dict[str, Any]]:
    """Mỗi vành đai khoảng cách có ít nhất một đại diện trong top `k`.

    Không có luật này, spatial decay hàm mũ dồn gần như toàn bộ top về vành đai
    gần nhất, và người dùng mất hẳn lựa chọn "đi xa hơn một chút nhưng tốt hơn".
    Chỉ bổ sung vành đai NÀO ĐANG THIẾU, không đảo thứ tự những cái còn lại.
    """
    if len(results) <= k:
        return results
    ordered = list(results)
    present = {_distance_band(item.get("distanceMeters") or 0.0) for item in ordered[:k]}
    for band in (0, 1, 2):
        if band in present:
            continue
        donor = next(
            (
                index
                for index in range(k, len(ordered))
                if _distance_band(ordered[index].get("distanceMeters") or 0.0) == band
            ),
            None,
        )
        if donor is None:
            continue  # không có ứng viên nào ở vành đai này, không ép
        # Hi sinh vị trí cuối của vành đai đang chiếm nhiều chỗ nhất trong top.
        counts: dict[int, int] = {}
        for item in ordered[:k]:
            item_band = _distance_band(item.get("distanceMeters") or 0.0)
            counts[item_band] = counts.get(item_band, 0) + 1
        crowded = max(counts, key=lambda b: counts[b])
        victim = max(
            index
            for index in range(k)
            if _distance_band(ordered[index].get("distanceMeters") or 0.0) == crowded
        )
        ordered[victim], ordered[donor] = ordered[donor], ordered[victim]
        present.add(band)
    return ordered


def insert_sponsored(
    results: list[dict[str, Any]], slots: tuple[int, ...] = (2,)
) -> list[dict[str, Any]]:
    """Chèn địa điểm tài trợ vào các vị trí `slots` (0-based).

    Ba ràng buộc đạo đức, cố ý cứng trong code chứ không để cấu hình:

    1. Địa điểm tài trợ VẪN phải qua bộ lọc địa lý — nó phải nằm sẵn trong tập
       kết quả, hàm này chỉ đổi vị trí, không bao giờ thêm POI mới vào.
    2. KHÔNG được chèn vào vị trí số 1. Kết quả đầu tiên là thứ người dùng tin
       tưởng nhất; bán chỗ đó là đánh đổi lòng tin lấy doanh thu.
    3. Luôn đánh dấu ``sponsored: true`` để giao diện hiển thị nhãn "Tài trợ".
       Chính sự minh bạch này mới là điều đáng bảo vệ, không phải cơ chế chèn.
    """
    sponsored = [item for item in results if item.get("sponsored")]
    if not sponsored:
        return results
    organic = [item for item in results if not item.get("sponsored")]
    ordered = list(organic)
    for slot, item in zip(sorted(s for s in slots if s >= 1), sponsored):
        ordered.insert(min(slot, len(ordered)), item)
    return ordered


def retrieve_candidates(
    latitude: float,
    longitude: float,
    radius: int,
    query_text: str | None,
    category: str | None,
    limit_candidates: int,
    telemetry: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Truy xuất ứng viên đa kênh qua OpenSearch, tự rơi về PostGIS khi cần.

    Trả về (candidates, backend) với backend là "opensearch" hoặc "postgis" để
    quan sát/observability biết đường nào đã được dùng.

    ``telemetry`` (nếu truyền vào) được điền thêm cách kênh không gian đã lọc:
    vành hexagon H3 hay geo_distance. Đường PostGIS không dùng cả hai nên ghi
    "postgis" — để không ai đọc nhầm một lần fallback thành bằng chứng về H3.
    """
    candidates = multi_channel_candidates(
        latitude, longitude, radius, query_text, category, limit_candidates,
        telemetry=telemetry,
    )
    if candidates is not None:
        return candidates, "opensearch"
    if telemetry is not None:
        telemetry.clear()
        telemetry["geoFilter"] = "postgis"
    fallback = fetch_candidates(
        latitude, longitude, radius, query_text, category, limit_candidates
    )
    return fallback, "postgis"


def rank_pois_detailed(
    latitude: float,
    longitude: float,
    radius: int,
    query_text: str | None,
    category: str | None,
    limit: int,
    category_boost: dict[str, float] | None = None,
    graph_boost: set[str] | None = None,
    ranker: str = "linear",
    telemetry: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Như `rank_pois` nhưng trả kèm backend truy xuất đã dùng.

    Backend ("opensearch" | "postgis") phải lộ ra ngoài API: hệ thống rơi về
    PostGIS một cách im lặng, nên nếu không hiển thị thì không ai biết số liệu
    đo được là của kiến trúc đa kênh hay của đường dự phòng.
    """
    candidates, backend = retrieve_candidates(
        latitude,
        longitude,
        radius,
        query_text,
        category,
        limit_candidates=max(limit * 4, 100),
        telemetry=telemetry,
    )
    candidates = apply_trending_boost(candidates, latitude, longitude)
    candidates = apply_crowd_signal(candidates, latitude, longitude, radius)
    candidates = enrich_candidates(candidates, latitude=latitude, longitude=longitude)
    candidates = attach_region_ctr(candidates)
    candidates = rerank(
        candidates,
        category_boost=category_boost,
        graph_boost=graph_boost,
        ranker=ranker,
        has_query_text=bool(query_text and query_text.strip()),
    )
    # Diversity & Business Rules, theo thứ tự: đa dạng loại + thương hiệu trước
    # (chúng sắp xếp lại cả danh sách), rồi hai luật chỉ vá chỗ thiếu trong top,
    # cuối cùng mới chèn tài trợ để nó không bị các luật sau đẩy đi chỗ khác.
    candidates = diversify(candidates)
    candidates = ensure_price_diversity(candidates, k=limit)
    candidates = ensure_distance_diversity(candidates, k=limit)
    candidates = insert_sponsored(candidates)
    return candidates[:limit], backend


def rank_pois(
    latitude: float,
    longitude: float,
    radius: int,
    query_text: str | None,
    category: str | None,
    limit: int,
    category_boost: dict[str, float] | None = None,
    graph_boost: set[str] | None = None,
    ranker: str = "linear",
) -> list[dict[str, Any]]:
    results, _backend = rank_pois_detailed(
        latitude,
        longitude,
        radius,
        query_text,
        category,
        limit,
        category_boost=category_boost,
        graph_boost=graph_boost,
        ranker=ranker,
    )
    return results


def fetch_categories() -> list[dict[str, Any]]:
    """Nhóm theo NHÃN (category_label), không theo mã category chi tiết.

    Nhiều mã OSM khác nhau (convenience/clothes/supermarket/electronics/
    shopping_mall...) cùng chung một nhãn tiếng Việt ("Mua sắm"). Nhóm theo mã
    thì giao diện hiện nhiều chip trùng chữ ("Mua sắm" lặp lại 5 lần) — người
    dùng không phân biệt được các mã con nên đó chỉ là trùng lặp vô nghĩa.
    Giá trị trả về ở cột "category" CHÍNH LÀ nhãn, dùng luôn làm tham số lọc
    (xem `fetch_candidates`/`multi_channel_candidates` lọc theo category_label).
    """
    query = """
        SELECT category_label AS category, category_label AS "categoryLabel", COUNT(*)::int AS count
        FROM pois
        WHERE category_label IS NOT NULL
        GROUP BY category_label
        ORDER BY count DESC, category_label ASC
    """
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return list(cursor.fetchall())


def fetch_trending(limit: int) -> dict[str, Any]:
    trending_poi_ids: list[str] = []
    trending_queries: list[dict[str, Any]] = []
    redis_ok = False
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1.5)
        client.ping()
        # Gộp ba khung giờ gần nhất với hệ số 1.0 / 0.5 / 0.25 thay vì đọc một
        # sorted set cộng dồn vĩnh viễn: "trending" giờ có biên thời gian.
        poi_scores = geo_cache.trending_pois(client=client)
        trending_poi_ids = [
            poi_id
            for poi_id, _score in sorted(
                poi_scores.items(), key=lambda item: item[1], reverse=True
            )[:limit]
        ]
        query_scores = geo_cache.trending_queries(client=client)
        trending_queries = [
            {"query": query_text, "score": score}
            for query_text, score in sorted(
                query_scores.items(), key=lambda item: item[1], reverse=True
            )[:limit]
        ]
        redis_ok = True
    except (redis.RedisError, OSError):
        pass

    pois: list[dict[str, Any]] = []
    if trending_poi_ids:
        query = """
            SELECT
                id::text AS id, name, description, category,
                category_label AS "categoryLabel", address,
                ST_Y(location::geometry) AS latitude,
                ST_X(location::geometry) AS longitude,
                rating::float8 AS rating, review_count AS "reviewCount",
                popularity_score AS "popularityScore"
                , opening_hours AS "openingHours", timezone,
                open_now AS "cachedOpenNow", price_level AS "priceLevel",
                (sponsored_until IS NOT NULL AND sponsored_until > NOW()) AS sponsored,
                amenities, tags, brand, district, city,
                country_code AS "countryCode", source, source_id AS "sourceId",
                canonical_id::text AS "canonicalId",
                jsonb_build_object('r7', h3_r7, 'r8', h3_r8, 'r9', h3_r9) AS "h3Cells",
                embedding_model AS "embeddingModel", updated_at AS "updatedAt"
            FROM pois
            WHERE id::text = ANY(%(ids)s)
        """
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, {"ids": trending_poi_ids})
                by_id = {
                    row["id"]: enrich_poi_response(dict(row))
                    for row in cursor.fetchall()
                }
        pois = [by_id[poi_id] for poi_id in trending_poi_ids if poi_id in by_id]

    return {
        "redisConnected": redis_ok,
        "pois": pois,
        "queries": trending_queries,
    }


def fetch_category_affinity(session_id: str) -> dict[str, float]:
    """Trọng số category ưa thích của một session, dựa trên poi_click /
    navigation_start / review trong ingestion_events — cùng trọng số với
    trending POI trong stream_worker.py để nhất quán tín hiệu."""
    query = """
        SELECT p.category AS category, e.event_type AS event_type, COUNT(*)::int AS occurrences
        FROM ingestion_events e
        JOIN pois p ON p.id::text = e.poi_id
        WHERE e.session_id = %(session_id)s
          AND e.event_type IN ('poi_click', 'navigation_start', 'review')
        GROUP BY p.category, e.event_type
    """
    weights = {"poi_click": 1.0, "navigation_start": 3.0, "review": 5.0}
    affinity: dict[str, float] = {}
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, {"session_id": session_id})
            for row in cursor.fetchall():
                affinity[row["category"]] = affinity.get(row["category"], 0.0) + weights[
                    row["event_type"]
                ] * row["occurrences"]
    if not affinity:
        return {}
    max_weight = max(affinity.values())
    # Chuẩn hóa về [0, 0.2] để cộng thêm vào score như một boost có kiểm soát.
    return {category: 0.2 * (value / max_weight) for category, value in affinity.items()}
