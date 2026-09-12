from copy import deepcopy

from app.features import serving
from app.ranking import rerank


def _candidate(poi_id, district="Quận 1", category="cafe"):
    return {
        "id": poi_id,
        "district": district,
        "category": category,
        "distanceMeters": 500,
        "textScore": 0.5,
        "rating": 4.0,
        "popularityScore": 0.5,
    }


def test_attach_region_ctr_maps_by_region(monkeypatch):
    monkeypatch.setattr(
        serving, "get_online_features",
        lambda view, keys: {"Quận 1|cafe": {"ctr": "0.42"}},
    )
    out = serving.attach_region_ctr([_candidate("a"), _candidate("b", district="Quận 3")])

    assert out[0]["regionCtr"] == 0.42
    assert out[1]["regionCtr"] == 0.0  # vùng chưa có feature -> 0, không vỡ


def test_attach_region_ctr_survives_empty_store(monkeypatch):
    monkeypatch.setattr(serving, "get_online_features", lambda view, keys: {})
    out = serving.attach_region_ctr([_candidate("a")])
    assert out[0]["regionCtr"] == 0.0


def test_profile_category_boost_scales_to_same_range_as_postgres_path():
    boost = serving.profile_category_boost({"affinity": {"cafe": 1.0, "park": 0.5}})
    assert boost == {"cafe": 0.2, "park": 0.1}  # cùng thang tối đa 0.2


def test_profile_category_boost_handles_missing_profile():
    assert serving.profile_category_boost(None) == {}
    assert serving.profile_category_boost({"affinity": {}}) == {}
    assert serving.profile_category_boost({"event_count": 3}) == {}


def test_region_ctr_raises_score_in_rerank():
    plain = rerank(deepcopy([_candidate("a")]))[0]["score"]
    with_ctr = deepcopy([_candidate("a")])
    with_ctr[0]["regionCtr"] = 1.0
    boosted = rerank(with_ctr)[0]["score"]

    assert boosted > plain
