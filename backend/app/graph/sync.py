"""Đồng bộ Knowledge Graph từ PostGIS + ingestion_events.

Chạy: ``python -m app.graph.sync`` (``--recreate`` để xóa và dựng lại). Đây là
indexing worker bản batch cho đồ thị; PostGIS vẫn là nguồn sự thật.
"""

from __future__ import annotations

import argparse
import logging

import psycopg
from psycopg.rows import dict_row

from ..config import settings
from .client import get_driver, is_graph_configured

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("nearby-graph-sync")

_CONSTRAINTS = [
    "CREATE CONSTRAINT poi_id IF NOT EXISTS FOR (p:Poi) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT session_id IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT district_name IF NOT EXISTS FOR (d:District) REQUIRE d.name IS UNIQUE",
]

_UPSERT_POIS = """
UNWIND $rows AS row
MERGE (p:Poi {id: row.id})
  SET p.name = row.name, p.category = row.category, p.district = row.district
MERGE (c:Category {name: row.category})
MERGE (p)-[:BELONGS_TO]->(c)
FOREACH (_ IN CASE WHEN row.district IS NULL THEN [] ELSE [1] END |
  MERGE (d:District {name: row.district})
  MERGE (p)-[:LOCATED_IN]->(d))
"""

_UPSERT_CLICKS = """
UNWIND $rows AS row
MERGE (s:Session {id: row.session_id})
MERGE (p:Poi {id: row.poi_id})
MERGE (s)-[c:CLICKED]->(p)
  SET c.weight = row.weight
"""

# SIMILAR_TO suy ra từ đồng-click: hai POI được cùng một phiên click thì liên
# quan; trọng số = số phiên chung. Tạo cạnh hai chiều để truy vấn tiện.
_BUILD_SIMILAR = """
MATCH (s:Session)-[:CLICKED]->(a:Poi), (s)-[:CLICKED]->(b:Poi)
WHERE a.id < b.id
WITH a, b, count(DISTINCT s) AS shared
WHERE shared >= $min_shared
MERGE (a)-[r:SIMILAR_TO]->(b) SET r.weight = shared
MERGE (b)-[r2:SIMILAR_TO]->(a) SET r2.weight = shared
"""

_SELECT_POIS = """
    SELECT id::text AS id, name, category, district
    FROM pois
"""

_SELECT_CLICKS = """
    SELECT session_id::text AS session_id, poi_id, COUNT(*)::int AS weight
    FROM ingestion_events
    WHERE poi_id IS NOT NULL
      AND event_type IN ('poi_click', 'navigation_start', 'review')
    GROUP BY session_id, poi_id
"""


def _fetch(database_url: str, query: str) -> list[dict]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]


def _write(driver, cypher: str, **params) -> None:
    driver.execute_query(cypher, params, database_=settings.neo4j_database)


def sync_graph(recreate: bool = False, min_shared: int = 1, batch_size: int = 1000) -> dict[str, int]:
    if not is_graph_configured():
        logger.error("Neo4j chưa cấu hình (NEO4J_URL/NEO4J_PASSWORD rỗng hoặc GRAPH_BACKEND=off)")
        return {}
    driver = get_driver()
    if driver is None:
        logger.error("Không tạo được Neo4j driver")
        return {}

    for constraint in _CONSTRAINTS:
        _write(driver, constraint)
    if recreate:
        logger.info("Xóa toàn bộ đồ thị cũ")
        _write(driver, "MATCH (n) DETACH DELETE n")
        for constraint in _CONSTRAINTS:
            _write(driver, constraint)

    pois = _fetch(settings.database_url, _SELECT_POIS)
    for start in range(0, len(pois), batch_size):
        _write(driver, _UPSERT_POIS, rows=pois[start : start + batch_size])

    clicks = _fetch(settings.database_url, _SELECT_CLICKS)
    for start in range(0, len(clicks), batch_size):
        _write(driver, _UPSERT_CLICKS, rows=clicks[start : start + batch_size])

    _write(driver, "MATCH (:Poi)-[r:SIMILAR_TO]->(:Poi) DELETE r")
    _write(driver, _BUILD_SIMILAR, min_shared=min_shared)

    stats = {"pois": len(pois), "clicks": len(clicks)}
    logger.info("Đồng bộ đồ thị: %s POI, %s cạnh click", stats["pois"], stats["clicks"])
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Đồng bộ Knowledge Graph từ PostGIS")
    parser.add_argument("--recreate", action="store_true", help="Xóa và dựng lại đồ thị")
    parser.add_argument("--min-shared", type=int, default=1, help="Số phiên chung tối thiểu cho SIMILAR_TO")
    args = parser.parse_args()
    sync_graph(recreate=args.recreate, min_shared=args.min_shared)


if __name__ == "__main__":
    main()
