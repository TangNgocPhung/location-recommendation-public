from app.graph import cypher


def test_also_liked_is_collaborative_and_parameterized():
    q = cypher.ALSO_LIKED
    assert "$session_id" in q and "$limit" in q
    assert ":CLICKED" in q
    # loại POI mà chính phiên hiện tại đã click
    assert "NOT (s)-[:CLICKED]->(rec)" in q
    assert "ORDER BY" in q


def test_related_pois_uses_similar_edge():
    q = cypher.RELATED_POIS
    assert ":SIMILAR_TO" in q
    assert "$poi_ids" in q
    assert "NOT rec.id IN $poi_ids" in q  # không trả lại chính hạt giống


def test_graph_stats_counts_all_entities():
    q = cypher.GRAPH_STATS
    for token in ("Poi", "Session", "CLICKED", "SIMILAR_TO"):
        assert token in q
