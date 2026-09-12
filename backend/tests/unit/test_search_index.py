from app.poi_features import EMBEDDING_DIMENSION
from app.search.index import build_document, index_mappings, index_settings


def _row(**overrides):
    row = {
        "id": "poi-1",
        "name": "Cà Phê Sài Gòn",
        "description": "Quán cà phê",
        "category": "cafe",
        "category_label": "Cà phê",
        "tags": ["cafe", "coffee"],
        "brand": None,
        "district": "Quận 1",
        "price_level": 2,
        "rating": 4.5,
        "popularity_score": 0.8,
        "latitude": 10.77,
        "longitude": 106.70,
        "h3_r7": "871a",
        "h3_r8": "881a",
        "h3_r9": "891a",
        "updated_at": None,
        "embedding": [0.1] * EMBEDDING_DIMENSION,
    }
    row.update(overrides)
    return row


def test_build_document_maps_location_as_geo_point() -> None:
    document = build_document(_row())

    assert document["location"] == {"lat": 10.77, "lon": 106.70}
    assert document["poi_id"] == "poi-1"
    assert document["embedding"] == [0.1] * EMBEDDING_DIMENSION


def test_build_document_regenerates_missing_embedding() -> None:
    document = build_document(_row(embedding=None))

    assert len(document["embedding"]) == EMBEDDING_DIMENSION
    # embedding tái tạo phải là vector đã chuẩn hóa (không toàn 0 với tên có chữ).
    assert any(value != 0 for value in document["embedding"])


def test_build_document_defaults_price_level_when_missing() -> None:
    document = build_document(_row(price_level=None))
    assert document["price_level"] == 0


def test_index_mapping_declares_geo_point_and_knn_vector() -> None:
    mappings = index_mappings()
    props = mappings["properties"]

    assert props["location"]["type"] == "geo_point"
    assert props["embedding"]["type"] == "knn_vector"
    assert props["embedding"]["dimension"] == EMBEDDING_DIMENSION


def test_index_settings_enable_knn_and_vietnamese_analyzer() -> None:
    body = index_settings()

    assert body["index"]["knn"] is True
    analyzers = body["analysis"]["analyzer"]
    assert "asciifolding" in analyzers["vi_folded"]["filter"]
