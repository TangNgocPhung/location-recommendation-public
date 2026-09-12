from app.search.fusion import fused_channels, reciprocal_rank_fusion


def test_rrf_rewards_documents_ranked_high_across_channels() -> None:
    channels = {
        "bm25": ["a", "b", "c"],
        "vector": ["b", "a", "d"],
        "geo": ["c", "b", "a"],
    }

    fused = reciprocal_rank_fusion(channels)
    ids = [poi_id for poi_id, _ in fused]

    # "a" và "b" xuất hiện đầu ở nhiều kênh nên phải đứng trên "c"/"d".
    assert ids[0] in {"a", "b"}
    assert set(ids[:2]) == {"a", "b"}
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_scores_descending_and_deterministic() -> None:
    channels = {"bm25": ["x", "y"], "geo": ["y", "x"]}

    fused = reciprocal_rank_fusion(channels)
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)
    # x và y đối xứng -> điểm bằng nhau, phá hòa bằng id tăng dần.
    assert [poi_id for poi_id, _ in fused] == ["x", "y"]


def test_rrf_weight_zero_drops_channel() -> None:
    channels = {"bm25": ["a"], "trending": ["z", "z2", "z3"]}

    fused = dict(reciprocal_rank_fusion(channels, weights={"trending": 0.0}))

    assert "a" in fused
    assert "z" not in fused


def test_rrf_ignores_duplicate_within_channel() -> None:
    fused = dict(reciprocal_rank_fusion({"bm25": ["a", "a", "b"]}))

    # "a" chỉ tính theo thứ hạng đầu, không cộng dồn.
    assert fused["a"] > fused["b"]


def test_fused_channels_reports_membership() -> None:
    membership = fused_channels({"bm25": ["a", "b"], "geo": ["b"]})

    assert membership["a"] == ["bm25"]
    assert membership["b"] == ["bm25", "geo"]
