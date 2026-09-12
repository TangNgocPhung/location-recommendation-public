"""Hydrate ứng viên từ PostGIS — nguồn dữ liệu chuẩn.

OpenSearch chỉ trả về poi_id đã xếp hạng. Ở đây ta lấy đầy đủ thuộc tính hiển
thị và tính khoảng cách chính xác từ PostGIS, đồng thời áp lại lọc bán kính/
category để loại các ứng viên lệch (ví dụ POI trending nằm ngoài vùng).
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..config import settings
from ..opening_hours import is_open_now

DATABASE_URL = settings.database_url

_HYDRATE_SQL = """
    SELECT
        id::text AS id, name, description, category,
        category_label AS "categoryLabel", address,
        ST_Y(location::geometry) AS latitude,
        ST_X(location::geometry) AS longitude,
        rating::float8 AS rating, review_count AS "reviewCount",
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
        ST_Distance(
            location,
            ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography
        ) AS "distanceMeters"
    FROM pois
    WHERE id::text = ANY(%(ids)s)
      AND ST_DWithin(
          location,
          ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography,
          %(radius)s
      )
      -- Lọc theo category_label (nhãn), không theo mã category chi tiết —
      -- cùng lý do với ranking.fetch_candidates: nhiều mã OSM khác nhau
      -- chung một nhãn, và người dùng chọn theo nhãn trên chip lọc.
      AND (CAST(%(category)s AS text) IS NULL OR category_label = %(category)s)
"""


def _finalize(row: dict[str, Any]) -> dict[str, Any]:
    row["openNow"] = is_open_now(row.get("openingHours"), row.get("timezone", "UTC"))
    row.pop("cachedOpenNow", None)
    return row


def hydrate_candidates(
    ranked: list[tuple[str, float]],
    latitude: float,
    longitude: float,
    radius: int,
    category: str | None,
    channels: dict[str, list[str]] | None = None,
    database_url: str | None = None,
    bm25_scores: dict[str, float] | None = None,
    vector_scores: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Lấy đầy đủ POI cho các id đã fusion, giữ thứ tự fusion.

    ``ranked``: danh sách (poi_id, fusion_score) giảm dần.
    ``channels``: poi_id -> các kênh đã truy xuất được (đính vào kết quả).
    ``bm25_scores``/``vector_scores``: poi_id -> điểm THÔ của riêng kênh đó.
    Trả về danh sách candidate hình dạng giống ``ranking.fetch_candidates``,
    có thêm ``fusionScore``, ``bm25Score``, ``vectorScore``, ``textScore`` và
    ``retrievalChannels``.
    """
    if not ranked:
        return []
    ids = [poi_id for poi_id, _ in ranked]
    fusion_scores = {poi_id: score for poi_id, score in ranked}
    params = {
        "ids": ids,
        "latitude": latitude,
        "longitude": longitude,
        "radius": radius,
        "category": category.strip() if category else None,
    }
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_HYDRATE_SQL, params)
            by_id = {row["id"]: _finalize(dict(row)) for row in cursor.fetchall()}

    max_fusion = max(fusion_scores.values()) if fusion_scores else 0.0
    bm25_scores = bm25_scores or {}
    max_bm25 = max(bm25_scores.values(), default=0.0)
    vector_scores = vector_scores or {}

    ordered: list[dict[str, Any]] = []
    for poi_id, score in ranked:
        candidate = by_id.get(poi_id)
        if candidate is None:  # rơi ngoài bán kính/category hoặc đã xóa khỏi DB
            continue
        candidate["fusionScore"] = score
        candidate["fusionScoreNorm"] = round(score / max_fusion, 6) if max_fusion else 0.0
        raw_bm25 = bm25_scores.get(poi_id)
        candidate["bm25Score"] = raw_bm25
        candidate["vectorScore"] = vector_scores.get(poi_id)
        # ``textScore`` phải là mức KHỚP VĂN BẢN, không phải điểm hợp nhất.
        #
        # Trước đây trường này lấy thẳng fusion đã chuẩn hóa. Hai hậu quả đo
        # được: (1) ứng viên hạng nhất của RRF luôn nhận trọn w_text = 0.26 dù
        # khớp văn bản dở, vì chuẩn hóa theo max; (2) RRF đã gộp cả kênh geo và
        # trending, nên hai tín hiệu đó bị cộng lần thứ hai qua w_spatial và
        # w_trending. Đó là lý do cấu hình "đầy đủ" từng đo thấp hơn baseline
        # PostGIS thuần (nDCG 0.4720 so với 0.9291).
        #
        # Không có truy vấn văn bản (duyệt theo vị trí) thì mọi ứng viên khớp
        # như nhau -> 1.0, giống nhánh PostGIS khi ``query_text`` là NULL.
        if not bm25_scores:
            candidate["textScore"] = 1.0
        elif raw_bm25 is None:
            # Lọt vào tập ứng viên qua kênh khác (geo/vector/trending) chứ
            # không qua BM25: không khớp văn bản, không phải "chưa biết".
            candidate["textScore"] = 0.0
        else:
            candidate["textScore"] = round(raw_bm25 / max_bm25, 6) if max_bm25 else 0.0
        candidate["retrievalChannels"] = (channels or {}).get(poi_id, [])
        ordered.append(candidate)
    return ordered
