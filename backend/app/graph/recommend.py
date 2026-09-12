"""Sinh candidate từ Knowledge Graph. Trả về danh sách poi_id đã xếp hạng, hoặc
rỗng khi Neo4j không dùng được (tầng trên tự fallback)."""

from __future__ import annotations

from . import cypher
from .client import graph_available, run_read


def also_liked_ids(session_id: str, limit: int = 50) -> list[str]:
    """POI mà 'người có hành vi tương tự cũng thích' theo đồ thị đồng-click."""
    if not session_id or not graph_available():
        return []
    rows = run_read(cypher.ALSO_LIKED, session_id=str(session_id), limit=limit)
    return [row["poi_id"] for row in rows]


def related_ids(poi_ids: list[str], limit: int = 50) -> list[str]:
    """POI liên quan (SIMILAR_TO) với một tập hạt giống."""
    if not poi_ids or not graph_available():
        return []
    rows = run_read(cypher.RELATED_POIS, poi_ids=[str(p) for p in poi_ids], limit=limit)
    return [row["poi_id"] for row in rows]


def graph_candidate_ids(
    session_id: str | None,
    seed_poi_ids: list[str] | None = None,
    limit: int = 50,
) -> list[str]:
    """Gộp 'cũng thích' và 'liên quan' thành một danh sách id không trùng, giữ
    thứ tự ưu tiên (collaborative trước, related sau)."""
    if not graph_available():
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for poi_id in also_liked_ids(session_id or "", limit) + related_ids(seed_poi_ids or [], limit):
        if poi_id not in seen:
            seen.add(poi_id)
            ordered.append(poi_id)
    return ordered[:limit]


def graph_stats() -> dict[str, int]:
    if not graph_available():
        return {}
    rows = run_read(cypher.GRAPH_STATS)
    return rows[0] if rows else {}
