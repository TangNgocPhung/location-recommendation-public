from copy import deepcopy

from app.ranking import rerank


def _candidate(poi_id):
    return {
        "id": poi_id,
        "category": "cafe",
        "distanceMeters": 500,
        "textScore": 0.5,
        "rating": 4.0,
        "popularityScore": 0.5,
    }


def test_graph_boost_raises_score_and_flags():
    base = rerank(deepcopy([_candidate("p1")]))[0]
    boosted = rerank(deepcopy([_candidate("p1")]), graph_boost={"p1"})[0]

    assert boosted["score"] > base["score"]
    assert boosted["graphRecommended"] is True
    assert base["graphRecommended"] is False


def test_graph_boost_can_reorder():
    a = _candidate("a")
    b = _candidate("b")
    # a và b giống hệt; chỉ b được đồ thị gợi ý -> b phải lên trước.
    ranked = rerank(deepcopy([a, b]), graph_boost={"b"})
    assert ranked[0]["id"] == "b"
