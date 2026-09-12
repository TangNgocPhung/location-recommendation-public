"""Mapping OpenSearch, analyzer tiếng Việt/không dấu và build document.

Chỉ mục lưu các trường phục vụ *truy xuất* (matching, geo, vector). Trường
hiển thị đầy đủ vẫn được hydrate từ PostGIS ở tầng ``enrichment`` để PostGIS
là nguồn sự thật duy nhất.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..config import settings
from ..poi_features import EMBEDDING_DIMENSION, normalize_text, text_embedding

logger = logging.getLogger("nearby-search")

INDEX_NAME = settings.opensearch_index

# ``asciifolding`` bỏ dấu ("cà phê" -> "ca phe") nên cả truy vấn có dấu lẫn
# không dấu đều khớp khi cùng đi qua analyzer này. ``vi_search`` thêm khả năng
# tìm tiền tố ngắn cho gợi ý gõ dở.
_ANALYSIS = {
    "filter": {
        "vi_edge_ngram": {
            "type": "edge_ngram",
            "min_gram": 2,
            "max_gram": 15,
        }
    },
    "analyzer": {
        # Bỏ dấu + lowercase, dùng khi index và khi query bình thường.
        "vi_folded": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "asciifolding"],
        },
        # Dùng riêng cho subfield prefix (autocomplete), chỉ áp lúc index.
        "vi_folded_ngram": {
            "type": "custom",
            "tokenizer": "standard",
            "filter": ["lowercase", "asciifolding", "vi_edge_ngram"],
        },
    },
}


def index_settings() -> dict[str, Any]:
    settings_body: dict[str, Any] = {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "analysis": _ANALYSIS,
    }
    if settings.opensearch_knn_enabled:
        settings_body["index"]["knn"] = True
    return settings_body


def index_mappings() -> dict[str, Any]:
    text_field = {
        "type": "text",
        "analyzer": "vi_folded",
        "fields": {
            "prefix": {"type": "text", "analyzer": "vi_folded_ngram", "search_analyzer": "vi_folded"},
        },
    }
    properties: dict[str, Any] = {
        "poi_id": {"type": "keyword"},
        "name": text_field,
        "normalized_name": {"type": "text", "analyzer": "vi_folded"},
        "description": {"type": "text", "analyzer": "vi_folded"},
        "category": {"type": "keyword"},
        "category_label": {"type": "text", "analyzer": "vi_folded", "fields": {"raw": {"type": "keyword"}}},
        "tags": {"type": "text", "analyzer": "vi_folded", "fields": {"raw": {"type": "keyword"}}},
        "brand": {"type": "text", "analyzer": "vi_folded"},
        "district": {"type": "keyword"},
        "price_level": {"type": "integer"},
        "rating": {"type": "float"},
        "popularity_score": {"type": "float"},
        "location": {"type": "geo_point"},
        "h3_r7": {"type": "keyword"},
        "h3_r8": {"type": "keyword"},
        "h3_r9": {"type": "keyword"},
        "updated_at": {"type": "date"},
    }
    if settings.opensearch_knn_enabled:
        properties["embedding"] = {
            "type": "knn_vector",
            "dimension": EMBEDDING_DIMENSION,
            "method": {
                "name": "hnsw",
                "space_type": "cosinesimil",
                "engine": "lucene",
            },
        }
    return {"properties": properties}


def ensure_index(client: Any) -> bool:
    """Tạo chỉ mục nếu chưa có. Trả về True nếu chỉ mục sẵn sàng.

    Kiểm-tra-rồi-tạo là hai lời gọi tách rời, nên luôn có khoảng trống ở giữa:
    một tiến trình khác (hoặc chính lần xoá ngay trước đó chưa kịp lan) có thể
    tạo chỉ mục xen vào, và `create` trả 400 resource_already_exists_exception
    làm đổ cả job indexing. Coi lỗi đó là THÀNH CÔNG: kết quả mong muốn —
    chỉ mục tồn tại — đã đạt được.
    """
    if client.indices.exists(index=INDEX_NAME):
        return True
    try:
        client.indices.create(
            index=INDEX_NAME,
            body={"settings": index_settings(), "mappings": index_mappings()},
        )
    except Exception as error:  # noqa: BLE001 - opensearchpy import trễ, không bắt kiểu cụ thể được
        if "resource_already_exists_exception" not in str(error):
            raise
        logger.info("Chỉ mục %s đã tồn tại (tạo đồng thời), bỏ qua", INDEX_NAME)
    return True


def _coerce_embedding(row: dict[str, Any]) -> list[float]:
    embedding = row.get("embedding")
    if embedding:
        return [float(value) for value in embedding]
    # POI cũ thiếu embedding: tái tạo tất định từ tên/mô tả/tags.
    return text_embedding((row.get("name"), row.get("description"), " ".join(row.get("tags") or [])))


def build_document(row: dict[str, Any]) -> dict[str, Any]:
    """Chuyển một bản ghi POI (từ PostGIS) thành document OpenSearch."""
    document: dict[str, Any] = {
        "poi_id": row["id"],
        "name": row.get("name") or "",
        "normalized_name": row.get("normalized_name") or normalize_text(row.get("name")),
        "description": row.get("description") or "",
        "category": row.get("category"),
        "category_label": row.get("category_label") or row.get("categoryLabel"),
        "tags": row.get("tags") or [],
        "brand": row.get("brand"),
        "district": row.get("district"),
        "price_level": row.get("price_level") if row.get("price_level") is not None else 0,
        "rating": float(row["rating"]) if row.get("rating") is not None else None,
        "popularity_score": float(row.get("popularity_score") or row.get("popularityScore") or 0.0),
        "location": {"lat": float(row["latitude"]), "lon": float(row["longitude"])},
        "h3_r7": row.get("h3_r7"),
        "h3_r8": row.get("h3_r8"),
        "h3_r9": row.get("h3_r9"),
        "updated_at": row.get("updated_at"),
    }
    if settings.opensearch_knn_enabled:
        document["embedding"] = _coerce_embedding(row)
    return document


def bulk_actions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dựng payload cho ``opensearchpy.helpers.bulk``."""
    actions: list[dict[str, Any]] = []
    for row in rows:
        document = build_document(row)
        actions.append(
            {
                "_op_type": "index",
                "_index": INDEX_NAME,
                "_id": document["poi_id"],
                "_source": document,
            }
        )
    return actions


def index_document(client: Any, row: dict[str, Any]) -> None:
    document = build_document(row)
    client.index(index=INDEX_NAME, id=document["poi_id"], body=document)


def delete_document(client: Any, poi_id: str) -> None:
    client.delete(index=INDEX_NAME, id=poi_id, ignore=[404])
