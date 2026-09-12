"""Kết nối Neo4j và cổng bật/tắt Knowledge Graph.

``neo4j`` driver được import trễ để unit test các hàm thuần trong ``cypher``
chạy được mà không cần cài package. Mọi lỗi kết nối bị nuốt và quy về "không
khả dụng"; có circuit breaker để mỗi request không phải chờ timeout khi Neo4j
sập (giống ``app.search.client``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import settings

logger = logging.getLogger("nearby-graph")

_DRIVER_CACHE: dict[str, Any] = {}
_DOWN_COOLDOWN_SECONDS = 15.0
_down_until = 0.0


def is_graph_configured() -> bool:
    if settings.graph_backend == "off":
        return False
    return bool(settings.neo4j_url and settings.neo4j_password)


def get_driver() -> Any | None:
    if not is_graph_configured():
        return None
    if "driver" in _DRIVER_CACHE:
        return _DRIVER_CACHE["driver"]
    try:
        from neo4j import GraphDatabase
    except ImportError:
        logger.warning("neo4j driver chưa được cài; bỏ qua Knowledge Graph")
        _DRIVER_CACHE["driver"] = None
        return None
    try:
        driver = GraphDatabase.driver(
            settings.neo4j_url,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=2.0,
            max_connection_lifetime=300,
        )
    except Exception as error:  # noqa: BLE001 - cấu hình sai không được làm sập app
        logger.warning("Không tạo được Neo4j driver: %s", error)
        driver = None
    _DRIVER_CACHE["driver"] = driver
    return driver


def graph_available() -> bool:
    """Cổng chính có circuit breaker: False tức thì trong cửa sổ cooldown sau
    một lần lỗi, ngược lại thử ``verify_connectivity`` một lần."""
    global _down_until
    if not is_graph_configured():
        return False
    now = time.monotonic()
    if now < _down_until:
        return False
    driver = get_driver()
    if driver is not None:
        try:
            driver.verify_connectivity()
            return True
        except Exception:  # noqa: BLE001
            pass
    _down_until = now + _DOWN_COOLDOWN_SECONDS
    _DRIVER_CACHE.pop("driver", None)
    return False


def run_read(cypher: str, **params: Any) -> list[dict[str, Any]]:
    """Chạy một câu Cypher chỉ-đọc, trả list dict. [] nếu Neo4j không dùng được."""
    driver = get_driver()
    if driver is None:
        return []
    try:
        records, _summary, _keys = driver.execute_query(
            cypher, params, database_=settings.neo4j_database, routing_="r"
        )
        return [record.data() for record in records]
    except Exception as error:  # noqa: BLE001
        logger.warning("Cypher đọc lỗi: %s", error)
        return []


def reset_driver_cache() -> None:
    global _down_until
    _DRIVER_CACHE.clear()
    _down_until = 0.0
