"""Các hàm thuần dựng truy vấn OpenSearch cho từng kênh truy xuất.

Tách khỏi client để test được mà không cần OpenSearch. Mỗi hàm trả về một
``body`` truyền thẳng vào ``client.search(index=..., body=body)``.
"""

from __future__ import annotations

from typing import Any

from ..config import settings


def _geo_filter(latitude: float, longitude: float, radius_m: int) -> dict[str, Any]:
    return {
        "geo_distance": {
            "distance": f"{radius_m}m",
            "location": {"lat": latitude, "lon": longitude},
        }
    }


def _category_filter(category: str | None) -> list[dict[str, Any]]:
    # Lọc theo category_label.raw (nhãn hiển thị, sub-field keyword), không
    # phải "category" (mã OSM chi tiết): nhiều mã chung một nhãn tiếng Việt,
    # và người dùng chọn theo nhãn trên chip lọc — xem ranking.fetch_categories.
    return [{"term": {"category_label.raw": category}}] if category else []



def _min_should_match() -> dict[str, Any]:
    """``minimum_should_match`` cho hai clause văn bản, hoặc rỗng khi tắt.

    Đọc `settings` mỗi lần gọi (không cache ở cấp module) để bật/tắt được
    trong CÙNG một tiến trình lúc đo A/B — nếu đọc lúc import thì phải dựng
    lại image cho từng giá trị, và khi đó hai lượt đo không còn chung tập dữ
    liệu trending/recency nữa.
    """
    value = (settings.search_text_min_should_match or "").strip()
    return {"minimum_should_match": value} if value else {}

def bm25_body(
    query_text: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    category: str | None,
    size: int,
) -> dict[str, Any]:
    """Kênh văn bản: multi_match có fuzzy (chịu lỗi chính tả), khớp không dấu
    nhờ analyzer ``vi_folded``, giới hạn theo bán kính và category.

    ``.strict`` (analyzer ``vi_strict``, giữ dấu thanh điệu) cộng thêm điểm
    khi khớp CHÍNH XÁC dấu thanh — đo được thật (Phase 10, 2026-09-12):
    asciifolding gộp nhầm "viện"/"viên" và "tấm"/"Tám" về cùng token, khiến
    "Công viên..." thắng "Bệnh viện..." cho truy vấn "bệnh viện".

    Tách thành HAI multi_match riêng trong ``should`` thay vì gộp chung một
    multi_match: field ``.strict`` KHÔNG được đặt ``fuzziness`` — nếu gộp
    chung, "AUTO" cho phép khoảng cách sửa 1 ký tự, và "viện" với "viên" chỉ
    khác nhau đúng 1 ký tự (ệ/ê) nên fuzzy sẽ lại khớp mờ, xoá sạch tác dụng
    phân biệt dấu thanh mà field này tồn tại để giải quyết. Hai clause cộng
    điểm (không phải lấy max) nên candidate khớp cả hai được thưởng thêm,
    còn candidate chỉ khớp nhờ fold vẫn giữ nguyên điểm cũ — không bị phạt,
    chỉ không được thưởng."""
    filters = _category_filter(category) + [_geo_filter(latitude, longitude, radius_m)]
    return {
        "size": size,
        "_source": ["poi_id"],
        "query": {
            "bool": {
                "must": [
                    {
                        "bool": {
                            "minimum_should_match": 1,
                            "should": [
                                {
                                    "multi_match": {
                                        "query": query_text,
                                        "type": "best_fields",
                                        "fields": [
                                            "name^3",
                                            "name.prefix^1.5",
                                            "category_label^2",
                                            "tags^1.5",
                                            "brand^1.5",
                                            "description",
                                        ],
                                        "fuzziness": "AUTO",
                                        "operator": "or",
                                        **_min_should_match(),
                                    }
                                },
                                {
                                    "multi_match": {
                                        "query": query_text,
                                        "type": "best_fields",
                                        "fields": [
                                            "name.strict^5",
                                            "category_label.strict^3",
                                            "tags.strict^2",
                                            "description.strict^1.5",
                                        ],
                                        "operator": "or",
                                        **_min_should_match(),
                                    }
                                },
                            ],
                        }
                    }
                ],
                "filter": filters,
            }
        },
    }


def geo_body(
    latitude: float,
    longitude: float,
    radius_m: int,
    category: str | None,
    size: int,
) -> dict[str, Any]:
    """Kênh không gian thuần: mọi POI trong bán kính, gần nhất trước. Bảo đảm
    recall địa lý ngay cả khi truy vấn văn bản rỗng."""
    filters = _category_filter(category) + [_geo_filter(latitude, longitude, radius_m)]
    return {
        "size": size,
        "_source": ["poi_id"],
        "query": {"bool": {"filter": filters}},
        "sort": [
            {
                "_geo_distance": {
                    "location": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "m",
                }
            }
        ],
    }


def h3_body(
    cells: tuple[str, ...] | list[str],
    field: str,
    latitude: float,
    longitude: float,
    category: str | None,
    size: int,
) -> dict[str, Any]:
    """Kênh không gian bằng **vành hexagon H3**: lọc bằng ``terms`` trên mã ô.

    Lọc là một phép tra chỉ mục đảo trên trường keyword, không phải phép tính
    khoảng cách trên từng document như ``geo_body``. Sắp xếp vẫn theo khoảng
    cách thật để ô gần tâm lên trước — H3 quyết định *ai được vào*, toạ độ
    quyết định *ai đứng trên*.

    Vành phủ trùm hình tròn nên tập trả về rộng hơn bán kính một chút;
    ``enrichment.hydrate_candidates`` cắt lại bằng ``ST_DWithin``.
    """
    filters = _category_filter(category) + [{"terms": {field: list(cells)}}]
    return {
        "size": size,
        "_source": ["poi_id"],
        "query": {"bool": {"filter": filters}},
        "sort": [
            {
                "_geo_distance": {
                    "location": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "m",
                }
            }
        ],
    }


def vector_body(
    embedding: list[float],
    latitude: float,
    longitude: float,
    radius_m: int,
    category: str | None,
    size: int,
) -> dict[str, Any]:
    """Kênh ngữ nghĩa: k-NN trên embedding, lọc theo bán kính/category. Dùng
    ``knn`` có filter (OpenSearch >= 2.4, engine lucene)."""
    filters = _category_filter(category) + [_geo_filter(latitude, longitude, radius_m)]
    return {
        "size": size,
        "_source": ["poi_id"],
        "query": {
            "knn": {
                "embedding": {
                    "vector": embedding,
                    "k": size,
                    "filter": {"bool": {"filter": filters}},
                }
            }
        },
    }


def extract_ranked_hits(response: dict[str, Any]) -> list[tuple[str, float]]:
    """Lấy (poi_id, _score) theo thứ tự hit từ response OpenSearch.

    Điểm thô cần thiết cho kênh BM25: sau RRF chỉ còn thứ hạng, mà thứ hạng
    không phân biệt được "khớp văn bản rất tốt" với "khớp tạm được nhưng là cái
    tốt nhất trong một tập kém". Tín hiệu ``text`` của bộ xếp hạng cần mức khớp
    thật, không phải vị trí.
    """
    hits = (response or {}).get("hits", {}).get("hits", [])
    ranked: list[tuple[str, float]] = []
    for hit in hits:
        source = hit.get("_source") or {}
        poi_id = source.get("poi_id") or hit.get("_id")
        if poi_id is None:
            continue
        try:
            score = float(hit.get("_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        ranked.append((str(poi_id), score))
    return ranked


def extract_ranked_ids(response: dict[str, Any]) -> list[str]:
    """Lấy danh sách poi_id theo thứ tự hit từ response OpenSearch."""
    return [poi_id for poi_id, _score in extract_ranked_hits(response)]
