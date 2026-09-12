import math

import h3
import pytest

from app.poi_features import (
    EMBEDDING_DIMENSION,
    dedupe_fingerprint,
    h3_cells,
    normalize_osm_element,
    normalize_district,
    normalize_text,
    text_embedding,
)
from app.poi_import import build_overpass_query, parse_bbox


def test_vietnamese_text_normalization_is_deterministic() -> None:
    assert normalize_text("  Cà phê Đường Sách! ") == "ca phe duong sach"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("District 1", "Quận 1"),
        ("Quan 7", "Quận 7"),
        ("Quận Tân Bình", "Quận Tân Bình"),
        ("Bình Thạnh", "Quận Bình Thạnh"),
        ("Xuân Hòa", None),
    ],
)
def test_district_normalization_rejects_ward_names(raw: str, expected: str | None) -> None:
    assert normalize_district(raw) == expected


def test_embedding_has_fixed_dimension_and_unit_norm() -> None:
    first = text_embedding(("Cà phê", "không gian yên tĩnh", "wifi"))
    second = text_embedding(("Cà phê", "không gian yên tĩnh", "wifi"))
    assert first == second
    assert len(first) == EMBEDDING_DIMENSION
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize(
    "word",
    ["phở", "bún", "cơm", "bánh", "cà", "phê", "trà", "mì", "hủ", "tiếu", "chợ", "quán"],
)
def test_single_word_query_never_yields_zero_vector(word: str) -> None:
    """Vector toàn 0 làm OpenSearch từ chối k-NN và kênh Vector ANN chết âm thầm.

    Ở v1 hai slot băm có thể trùng nhau và triệt tiêu; "phở" là một ca dính lỗi
    thật, đúng từ khoá ví dụ của đề tài.
    """
    assert any(text_embedding((word,)))


def test_embedding_slots_never_collide_across_alphabet() -> None:
    """Quét rộng: không từ đơn nào trong bảng chữ cái tiếng Việt không dấu được
    phép cho vector 0, vì slot thứ hai luôn lệch khỏi slot thứ nhất."""
    alphabet = "abcdefghiklmnopqrstuvxy"
    empty = [
        first + second
        for first in alphabet
        for second in alphabet
        if not any(text_embedding((first + second,)))
    ]
    assert empty == []


def test_h3_cells_have_expected_resolutions() -> None:
    cells = h3_cells(10.7757, 106.7009)
    assert {key: h3.get_resolution(value) for key, value in cells.items()} == {
        "r7": 7,
        "r8": 8,
        "r9": 9,
    }


def test_osm_element_is_mapped_to_canonical_shape() -> None:
    poi = normalize_osm_element(
        {
            "type": "node",
            "id": 123,
            "lat": 10.7757,
            "lon": 106.7009,
            "tags": {
                "name": "Cà phê Thử Nghiệm",
                "amenity": "cafe",
                "opening_hours": "Mo-Su 07:00-22:00",
                "addr:district": "Quận 1",
                "internet_access": "wlan",
            },
        }
    )
    assert poi is not None
    assert poi["category"] == "cafe"
    assert poi["source_id"] == "node/123"
    assert poi["district"] == "Quận 1"
    assert len(poi["embedding"]) == EMBEDDING_DIMENSION
    assert poi["amenities"]["internet_access"] == "wlan"


def test_bbox_and_overpass_query_are_bounded() -> None:
    bbox = parse_bbox("10.70,106.60,10.90,106.82")
    query = build_overpass_query(bbox)
    assert 'nwr["name"]["amenity"' in query
    assert "10.7,106.6,10.9,106.82" in query


def test_fingerprint_changes_with_location() -> None:
    first = dedupe_fingerprint("Cà phê A", "cafe", 10.77, 106.70)
    second = dedupe_fingerprint("Cà phê A", "cafe", 10.78, 106.70)
    assert first != second
