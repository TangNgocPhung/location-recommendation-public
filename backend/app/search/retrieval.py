"""Điều phối truy xuất đa kênh: BM25 + geo + vector + trending -> RRF -> hydrate.

Trả về danh sách candidate đã hydrate từ PostGIS, hoặc ``None`` khi OpenSearch
không khả dụng / lỗi để ``ranking.rank_pois`` rơi về đường PostGIS thuần.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import geo_cache
from ..config import settings
from ..poi_features import h3_ring_geometry, h3_ring_ids, text_embedding
from . import query as query_builder
from .client import get_client, search_available
from .enrichment import hydrate_candidates
from .fusion import fused_channels, reciprocal_rank_fusion
from .index import INDEX_NAME

logger = logging.getLogger("nearby-search")


# Trọng số kênh trong RRF. Text và vector là tín hiệu liên quan mạnh nhất;
# hai kênh không gian bảo đảm recall; trending là gia vị thời gian thực.
#
# `h3` và `geo` cùng mô tả MỘT chiều — vị trí — và tập kết quả của chúng chồng
# lên nhau gần hết. Nên tổng trọng số của hai kênh này được giữ đúng bằng 0.6
# của kênh `geo` đơn lẻ trước đây, thay vì cộng thêm một kênh mới vào.
#
# Nếu không chia mà để `h3` = 0.6 nữa thì chiều không gian tự nhiên nặng gấp
# đôi so với lúc đo Phase 3, và mọi so sánh nDCG trước/sau H3 sẽ đo nhầm: cái
# thay đổi là TRỌNG SỐ chứ không phải cách lọc. Đây đúng là kiểu lỗi mà Phase 3
# đã mất một vòng để tìm ra (geo và trending bị cộng hai lần qua RRF).
#
# Phần lớn hơn thuộc về `geo` vì đó là kênh lọc TINH (đúng bán kính); `h3` là
# kênh lọc THÔ (vành hexagon phủ trùm, có POI ngoài bán kính).
CHANNEL_WEIGHTS = {
    "bm25": 1.0,
    "vector": 0.9,
    "geo": 0.35,
    "h3": 0.25,
    "trending": 0.5,
}

# Số hit lấy về mỗi kênh trước khi fusion.
PER_CHANNEL_SIZE = 150


def _search_hits(client: Any, body: dict[str, Any]) -> list[tuple[str, float]]:
    response = client.search(index=INDEX_NAME, body=body)
    return query_builder.extract_ranked_hits(response)


def _search_ids(client: Any, body: dict[str, Any]) -> list[str]:
    return [poi_id for poi_id, _score in _search_hits(client, body)]


def _trending_ids(limit: int, latitude: float | None = None, longitude: float | None = None) -> list[str]:
    """Kênh trending: ưu tiên ô H3 quanh tâm tìm kiếm, rơi về toàn cục.

    Cả hai đều đã gộp ba khung giờ gần nhất, nên kênh này mang POI đang hot
    *gần đây và lúc này*, không phải POI hot nhất kể từ lần khởi động Redis.
    """
    client = geo_cache.get_client()
    if client is None:
        return []
    scores: dict[str, float] = {}
    if latitude is not None and longitude is not None:
        scores = geo_cache.trending_pois_near(latitude, longitude, client=client)
    if not scores:
        scores = geo_cache.trending_pois(client=client)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [poi_id for poi_id, _score in ranked[:limit]]


def _spatial_channels(
    client: Any,
    latitude: float,
    longitude: float,
    radius: int,
    category: str | None,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Hai kênh không gian. Trả (kênh -> poi_id, mô tả đã chạy những gì).

    - ``geo``: ``geo_distance``, lọc TINH — đúng bán kính, tính khoảng cách
      trên từng document.
    - ``h3``: vành hexagon, lọc THÔ — một phép tra ``terms`` trên chỉ mục đảo.

    Giữ cả hai đúng như lộ trình B3: chúng lọc cùng một chiều bằng hai cơ chế
    khác nhau, nên POI xuất hiện ở cả hai là bằng chứng vị trí mạnh hơn POI chỉ
    lọt vành thô. Cấu hình ``geo_channel`` cho phép tắt từng kênh để ablation
    tách được đóng góp của riêng H3.
    """
    mode = settings.geo_channel
    channels: dict[str, list[str]] = {}
    info: dict[str, Any] = {"geoChannelMode": mode}

    if mode in ("both", "geo_distance"):
        channels["geo"] = _search_ids(
            client,
            query_builder.geo_body(latitude, longitude, radius, category, PER_CHANNEL_SIZE),
        )

    if mode in ("both", "h3"):
        ring = h3_ring_ids(latitude, longitude, radius, max_cells=settings.h3_ring_max_cells)
        if ring is None:
            # Bán kính lớn tới mức vành hexagon vượt trần số ô. Ghi lý do lại:
            # im lặng ở đây thì một bài đo trên bán kính lớn sẽ bị ghi công cho
            # H3 trong khi thực tế chỉ có geo_distance chạy.
            info["h3Skipped"] = "radius-too-large"
        else:
            channels["h3"] = _search_ids(
                client,
                query_builder.h3_body(
                    ring.cells, ring.field, latitude, longitude, category, PER_CHANNEL_SIZE
                ),
            )
            info.update(
                {
                    "h3Resolution": ring.resolution,
                    "h3RingK": ring.k,
                    "h3CellCount": len(ring.cells),
                    "h3Origin": ring.origin,
                }
            )
            # Hình học đi kèm để giao diện vẽ được đúng vùng đã quét. `info`
            # chảy thẳng ra trường geoFilter của response, nên thêm khoá ở đây
            # là xong — không phải sửa api.py hay models.py.
            outline = h3_ring_geometry(ring)
            if outline is not None:
                info["h3Outline"] = outline

    return channels, info


def _gate_by_text_relevance(
    fused: list[tuple[str, float]],
    channels: dict[str, list[str]],
    clean_query: str,
) -> list[tuple[str, float]]:
    """Có truy vấn chữ mà BM25 đã khớp được thì CHỈ trả candidate khớp chữ.

    Đo được thật (không phải suy đoán): truy vấn "bệnh viện" trả "Phở Nhà
    Mình" ở RANK 0 — không qua BM25, chỉ vì gần (174m) và đang trending. RRF
    gộp điểm cả 5 kênh nên một POI "gần + hot" có thể thắng một POI thật sự
    khớp truy vấn nhưng ở xa hơn.

    Bản trước chỉ ĐẨY candidate geo/h3/trending xuống sau, vẫn giữ lại để "bổ
    sung recall". Đo lại 19/09/2026: "bệnh viện" bán kính 1 km có đúng MỘT bệnh
    viện, 49 dòng còn lại là quán ăn, trường học, công viên — danh sách luôn đủ
    50 bất kể có liên quan hay không, và người dùng đọc thẳng nó là kết quả
    tìm kiếm. Nên giờ bỏ hẳn phần bổ sung khi đã có candidate khớp chữ.

    Chỉ BM25 được tính là bằng chứng liên quan. Kênh vector là embedding băm
    bag-of-words (``text_embedding``) và k-NN LUÔN trả đủ k hit gần nhất kể cả
    khi cosine ~ 0, nên mọi POI trong bán kính đều lọt vào kênh này — tính nó
    là "liên quan" thì cổng này không chặn được gì. Vector vẫn góp vào điểm
    RRF để xếp thứ tự bên trong nhóm khớp chữ.

    Không áp dụng khi không có query text (duyệt theo vị trí — geo là kênh
    chính đáng), và giữ nguyên RRF khi BM25 không khớp được gì (truy vấn quá
    lạ: trả thứ gần nhất vẫn hơn trả rỗng).
    """
    if not clean_query:
        return fused
    relevant_ids = set(channels.get("bm25", []))
    if not relevant_ids:
        return fused
    return [item for item in fused if item[0] in relevant_ids]


def multi_channel_candidates(
    latitude: float,
    longitude: float,
    radius: int,
    query_text: str | None,
    category: str | None,
    limit_candidates: int,
    telemetry: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Trả candidate đã hydrate, hoặc None nếu không dùng được OpenSearch."""
    if not search_available():
        return None
    client = get_client()
    if client is None:
        return None

    channels: dict[str, list[str]] = {}
    # Kênh đã CHẠY nhưng hỏng (timeout / k-NN lỗi). Khác hẳn kênh không chạy:
    # truy vấn vẫn trả `retrievalBackend="opensearch"` nên nếu không ghi lại ở
    # đây thì việc mất kênh là hoàn toàn vô hình — cả với người dùng lẫn với
    # script đánh giá đang tưởng mình đo kiến trúc đủ 3 kênh.
    degraded: list[str] = []
    bm25_scores: dict[str, float] = {}
    vector_scores: dict[str, float] = {}
    try:
        clean_query = query_text.strip() if query_text else ""
        if clean_query:
            bm25_hits = _search_hits(
                client,
                query_builder.bm25_body(
                    clean_query, latitude, longitude, radius, category, PER_CHANNEL_SIZE
                ),
            )
            channels["bm25"] = [poi_id for poi_id, _score in bm25_hits]
            bm25_scores = dict(bm25_hits)
            if settings.opensearch_knn_enabled:
                embedding = text_embedding((clean_query,))
                if not any(embedding):
                    # Vector 0 làm OpenSearch trả 400; bỏ kênh này thay vì để
                    # cả truy vấn rơi về PostGIS. Mức warning để lần sau thấy ngay.
                    logger.warning(
                        "Truy vấn %r cho embedding toàn 0, bỏ kênh vector lần này", clean_query
                    )
                    degraded.append("vector")
                else:
                    try:
                        vector_hits = _search_hits(
                            client,
                            query_builder.vector_body(
                                embedding, latitude, longitude, radius, category, PER_CHANNEL_SIZE
                            ),
                        )
                        channels["vector"] = [poi_id for poi_id, _score in vector_hits]
                        vector_scores = dict(vector_hits)
                    except Exception as error:  # noqa: BLE001 - k-NN có thể tắt/khác version
                        logger.warning("Kênh vector lỗi, bỏ qua: %s", error)
                        degraded.append("vector")
        spatial, spatial_info = _spatial_channels(
            client, latitude, longitude, radius, category
        )
        channels.update(spatial)
        if telemetry is not None:
            telemetry.update(spatial_info)
    except Exception as error:  # noqa: BLE001 - bất kỳ lỗi OpenSearch nào -> fallback
        logger.warning("Truy xuất OpenSearch lỗi, rơi về PostGIS: %s", error)
        return None

    # Kênh không gian là recall phổ quát: chỉ mục có dữ liệu và có POI trong bán
    # kính thì ít nhất một trong hai kênh luôn trả kết quả. Cả hai cùng rỗng =>
    # chỉ mục rỗng/chưa reindex hoặc vùng không có POI -> để PostGIS xử lý (an
    # toàn, không trả nhầm mỗi trending).
    if not channels.get("geo") and not channels.get("h3"):
        return None

    trending = _trending_ids(50, latitude, longitude)
    if trending:
        channels["trending"] = trending

    if telemetry is not None:
        telemetry["channelsUsed"] = sorted(channels)
        # Chỉ đặt khoá khi THỰC SỰ mất kênh: `None` trong response nghĩa là
        # "đủ kênh", không phải "không biết".
        telemetry["degradedChannels"] = sorted(set(degraded)) or None

    fused = reciprocal_rank_fusion(channels, weights=CHANNEL_WEIGHTS)
    ranked = _gate_by_text_relevance(fused, channels, clean_query)[:limit_candidates]
    membership = fused_channels(channels)
    try:
        return hydrate_candidates(
            ranked,
            latitude,
            longitude,
            radius,
            category,
            channels=membership,
            bm25_scores=bm25_scores,
            vector_scores=vector_scores,
        )
    except Exception as error:  # noqa: BLE001 - lỗi DB ở hydrate cũng nên fallback
        logger.warning("Hydrate ứng viên lỗi, rơi về PostGIS: %s", error)
        return None
