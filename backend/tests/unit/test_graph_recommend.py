from app.config import settings
from app.graph import recommend
from app.graph.client import is_graph_configured, reset_driver_cache


def test_not_configured_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "neo4j_url", "", raising=False)
    reset_driver_cache()
    assert is_graph_configured() is False
    assert recommend.graph_candidate_ids("sess-1") == []
    assert recommend.also_liked_ids("sess-1") == []
    assert recommend.related_ids(["p1"]) == []


def test_backend_off_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "graph_backend", "off", raising=False)
    monkeypatch.setattr(settings, "neo4j_url", "bolt://localhost:7687", raising=False)
    monkeypatch.setattr(settings, "neo4j_password", "x", raising=False)
    reset_driver_cache()
    assert is_graph_configured() is False
    assert recommend.graph_candidate_ids("sess-1") == []


def test_candidate_ids_dedup_and_order(monkeypatch):
    monkeypatch.setattr(recommend, "graph_available", lambda: True)
    monkeypatch.setattr(recommend, "also_liked_ids", lambda s, limit=50: ["a", "b", "c"])
    monkeypatch.setattr(recommend, "related_ids", lambda ids, limit=50: ["b", "d"])

    out = recommend.graph_candidate_ids("sess-1", seed_poi_ids=["x"], limit=10)

    assert out == ["a", "b", "c", "d"]  # collaborative trước, related sau, không trùng
