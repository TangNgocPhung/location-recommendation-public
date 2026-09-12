"""Diversity & Business Rules (bước B7).

Trước đây `diversify()` chỉ đọc duy nhất trường `category`. Ba luật còn lại của
ô "Diversity & Business Rules" trong sơ đồ kiến trúc chưa được cài đặt.
"""

from app.ranking import (
    diversify,
    ensure_distance_diversity,
    ensure_price_diversity,
    insert_sponsored,
)


def poi(poi_id, category="cafe", brand=None, price=0, distance=100.0, sponsored=False):
    return {
        "id": poi_id,
        "category": category,
        "brand": brand,
        "priceLevel": price,
        "distanceMeters": distance,
        "sponsored": sponsored,
    }


# --- Thương hiệu ------------------------------------------------------------


def test_brand_quota_pushes_extra_chain_stores_to_the_end():
    """Lỗi rất dễ bị bắt khi demo: tìm 'cà phê' ra 6 cửa hàng cùng chuỗi."""
    results = [poi(f"h{i}", brand="Highlands") for i in range(6)]
    ordered = diversify(results, max_per_brand=2)

    assert [item["id"] for item in ordered[:2]] == ["h0", "h1"]
    # Không loại hẳn — với bán kính nhỏ, loại hẳn làm hụt kết quả.
    assert len(ordered) == 6
    assert {item["id"] for item in ordered[2:]} == {"h2", "h3", "h4", "h5"}


def test_empty_brand_is_not_treated_as_a_chain():
    """Phần lớn POI từ OSM không có tag brand; gộp chúng thành một 'thương hiệu'
    sẽ đẩy gần hết kết quả xuống cuối."""
    results = [poi(f"p{i}", brand=None) for i in range(5)]
    assert len(diversify(results, max_per_brand=2)) == 5
    assert [item["id"] for item in diversify(results, max_per_brand=2)][:3] == ["p0", "p1", "p2"]


def test_category_run_limit_still_applies():
    results = [poi("a"), poi("b"), poi("c"), poi("d", category="park")]
    ordered = diversify(results, max_run=2)
    assert ordered[2]["category"] == "park"


# --- Giá --------------------------------------------------------------------


def test_price_diversity_pulls_affordable_places_into_the_top():
    results = [poi(f"x{i}", price=4) for i in range(10)] + [
        poi("cheap1", price=1),
        poi("cheap2", price=2),
    ]
    ordered = ensure_price_diversity(results, k=10, minimum_affordable=2)
    top_ids = {item["id"] for item in ordered[:10]}
    assert "cheap1" in top_ids and "cheap2" in top_ids


def test_price_level_zero_means_unknown_not_cheap():
    """93% POI thiếu dữ liệu giá; coi 0 là 'rẻ' làm luật này thành vô nghĩa."""
    results = [poi(f"x{i}", price=0) for i in range(10)] + [poi("cheap", price=1)]
    ordered = ensure_price_diversity(results, k=10, minimum_affordable=1)
    assert "cheap" in {item["id"] for item in ordered[:10]}


def test_price_rule_is_a_no_op_when_the_top_is_already_diverse():
    results = [poi("a", price=1), poi("b", price=2), poi("c", price=4)]
    assert ensure_price_diversity(results, k=10) == results


# --- Khoảng cách ------------------------------------------------------------


def test_each_distance_band_gets_a_representative():
    near = [poi(f"n{i}", distance=100.0) for i in range(10)]
    mid = poi("mid", distance=900.0)
    far = poi("far", distance=4_000.0)
    ordered = ensure_distance_diversity(near + [mid, far], k=10)

    top_ids = {item["id"] for item in ordered[:10]}
    assert "mid" in top_ids
    assert "far" in top_ids


def test_distance_rule_does_not_force_a_band_that_has_no_candidate():
    results = [poi(f"n{i}", distance=100.0) for i in range(12)]
    assert [item["id"] for item in ensure_distance_diversity(results, k=10)] == [
        item["id"] for item in results
    ]


# --- Tài trợ ----------------------------------------------------------------


def test_sponsored_never_takes_the_first_position():
    """Kết quả đầu tiên là thứ người dùng tin nhất; bán chỗ đó là đánh đổi lòng
    tin lấy doanh thu."""
    results = [poi("organic1"), poi("ad", sponsored=True), poi("organic2")]
    ordered = insert_sponsored(results, slots=(2,))
    assert ordered[0]["id"] == "organic1"
    assert ordered[0].get("sponsored") is False


def test_sponsored_goes_into_the_requested_slot():
    results = [poi("o1"), poi("o2"), poi("o3"), poi("ad", sponsored=True)]
    ordered = insert_sponsored(results, slots=(2,))
    assert ordered[2]["id"] == "ad"


def test_sponsored_never_adds_a_poi_that_was_not_retrieved():
    """Địa điểm tài trợ vẫn phải qua bộ lọc địa lý — hàm chỉ đổi vị trí."""
    results = [poi("o1"), poi("ad", sponsored=True)]
    ordered = insert_sponsored(results, slots=(2,))
    assert len(ordered) == 2
    assert {item["id"] for item in ordered} == {"o1", "ad"}


def test_no_sponsored_places_leaves_the_order_untouched():
    results = [poi("a"), poi("b")]
    assert insert_sponsored(results) == results
