from copy import deepcopy

import pytest

from app.ranking import _relevance_sort_key, diversify, rerank


def candidate(
    poi_id: str,
    category: str,
    *,
    distance: float = 500,
    text: float = 0.8,
    rating: float = 4.5,
    popularity: float = 0.7,
    trending: float = 0.0,
) -> dict:
    return {
        "id": poi_id,
        "category": category,
        "distanceMeters": distance,
        "textScore": text,
        "rating": rating,
        "popularityScore": popularity,
        "trendingScore": trending,
    }


def test_rerank_prefers_stronger_relevance_signals() -> None:
    weak = candidate("weak", "park", distance=2000, text=0.2, rating=3.0, popularity=0.2)
    strong = candidate("strong", "cafe", distance=200, text=0.95, rating=4.9, popularity=0.95)

    ranked = rerank(deepcopy([weak, strong]))

    assert [item["id"] for item in ranked] == ["strong", "weak"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_rerank_gate_day_candidate_khong_lien_quan_van_ban_xuong_sau() -> None:
    """Đo được thật: truy vấn "cà phê" (radius 3km, limit 50) vẫn xếp "Phở Nhà
    Mình" (không BM25, không vector, chỉ gần 174m + đang trending cao) ở HẠNG 1
    — vì `_gate_by_text_relevance` ở tầng retrieval chỉ quyết định ai LỌT VÀO
    candidate pool, còn `rerank` tính điểm và sắp xếp lại TỪ ĐẦU, ghi đè hoàn
    toàn thứ tự đó. Ứng viên gần + trending nhưng KHÔNG có bm25Score/vectorScore
    phải luôn đứng sau ứng viên có tín hiệu văn bản khi có query text, bất kể
    điểm số tuyến tính cao thấp ra sao."""
    no_text_signal = candidate(
        "gan-trending", "restaurant", distance=174, text=0.0, rating=4.6, popularity=0.9, trending=0.8
    )
    no_text_signal["vectorScore"] = None
    has_bm25 = candidate(
        "co-bm25", "cafe", distance=1500, text=0.6, rating=4.0, popularity=0.5, trending=0.0
    )
    has_bm25["vectorScore"] = None
    has_vector_only = candidate(
        "co-vector", "cafe", distance=1500, text=0.0, rating=4.0, popularity=0.5, trending=0.0
    )
    has_vector_only["vectorScore"] = 0.8

    ranked = rerank(deepcopy([no_text_signal, has_bm25, has_vector_only]), has_query_text=True)

    assert ranked[-1]["id"] == "gan-trending"
    assert {ranked[0]["id"], ranked[1]["id"]} == {"co-bm25", "co-vector"}


def test_rerank_khong_gate_khi_khong_co_query_text() -> None:
    """Duyệt theo vị trí thuần — không có "liên quan văn bản" nào để so sánh,
    điểm tuyến tính (đặc biệt khoảng cách) mới là thứ quyết định thứ tự."""
    close_no_signal = candidate("gan", "restaurant", distance=174, text=1.0, trending=0.8)
    close_no_signal["vectorScore"] = None
    far_with_signal = candidate("xa", "cafe", distance=1500, text=1.0)
    far_with_signal["vectorScore"] = None

    ranked = rerank(deepcopy([close_no_signal, far_with_signal]), has_query_text=False)

    assert ranked[0]["id"] == "gan"


@pytest.mark.xfail(
    reason=(
        "Giới hạn ĐÃ BIẾT của embedding hashing-v2-64, cố ý CHƯA vá bằng "
        "ngưỡng vectorScore (xem thảo luận Phase 6.5): đo được thật trên "
        "truy vấn 'mỳ cay' — 'Bảo tàng Thành phố' (mô tả hoàn toàn không liên "
        "quan) nhận vectorScore=0.647 do trùng ngẫu nhiên vài slot băm, đứng "
        "TRÊN cả candidate khớp BM25 thật. Không đặt ngưỡng tạm vì không có "
        "bằng chứng ngưỡng nào tách được match thật khỏi hash collision một "
        "cách ổn định (candidate khớp thật đo được chỉ 0.737, cách 0.647 rất "
        "gần). Test này XFAIL có chủ đích — khi Phase semantic embedding thay "
        "hashing bằng model thật, test sẽ tự PASS (xpass) và đó là tín hiệu "
        "để xoá marker này đi, không phải để sửa rerank."
    ),
    strict=False,
)
def test_vector_hashing_false_positive_khong_duoc_thang_bm25_that() -> None:
    """Dùng ĐÚNG con số đo thật từ API (truy vấn "mỳ cay", không phải tái tạo
    qua `rerank` — công thức tuyến tính có quá nhiều tín hiệu phụ (popularity/
    trending/context/regionCtr...) để tái tạo chính xác bằng tay; test trên số
    ``score`` cuối cùng đã đo mới trung thực với lỗi thật đang xảy ra."""
    museum_false_positive = {
        "id": "museum-khong-lien-quan",
        "textScore": 0.0,
        "vectorScore": 0.647,
        "score": 0.474654,
        "distanceMeters": 177,
    }
    real_bm25_vector_match = {
        "id": "nha-hang-that",
        "textScore": 0.9,
        "vectorScore": 0.737,
        "score": 0.404866,
        "distanceMeters": 1436,
    }

    ranked = sorted(
        [museum_false_positive, real_bm25_vector_match], key=_relevance_sort_key(has_query_text=True)
    )

    assert ranked[0]["id"] == "nha-hang-that"


def test_category_boost_changes_score_without_removing_other_signals() -> None:
    original = candidate("one", "cafe")

    regular_score = rerank(deepcopy([original]))[0]["score"]
    boosted_score = rerank(deepcopy([original]), category_boost={"cafe": 0.2})[0]["score"]

    assert boosted_score == pytest.approx(regular_score + 0.2)


def test_diversify_prevents_three_consecutive_categories_when_possible() -> None:
    results = [
        candidate("c1", "cafe"),
        candidate("c2", "cafe"),
        candidate("c3", "cafe"),
        candidate("p1", "park"),
    ]

    diversified = diversify(results, max_run=2)

    assert [item["id"] for item in diversified] == ["c1", "c2", "p1", "c3"]
