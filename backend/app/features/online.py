"""Online store trên Redis — đọc feature độ trễ thấp lúc serving.

Khóa và version lấy từ ``registry`` nên serving luôn dùng đúng định nghĩa
feature như lúc huấn luyện. Mọi lỗi Redis được nuốt và trả rỗng để đường gợi ý
vẫn chạy (fallback tính trực tiếp từ Postgres).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import redis

from ..config import settings
from .registry import REGISTRY, FeatureView, get_view

logger = logging.getLogger("nearby-features")

REDIS_URL = settings.redis_url
_TTL_SECONDS = 7 * 24 * 3600  # feature tự hết hạn nếu ngừng materialize


def _client() -> redis.Redis | None:
    try:
        return redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=1.5)
    except (redis.RedisError, OSError):
        return None


def _encode(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _decode(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def meta_key(view: FeatureView) -> str:
    return f"feat:{view.version}:{view.name}:_meta"


def write_features(view_name: str, rows: Iterable[dict[str, Any]]) -> int:
    """Ghi feature vào online store. Trả số entity đã ghi (0 nếu Redis lỗi)."""
    view = get_view(view_name)
    client = _client()
    if client is None:
        return 0
    written = 0
    try:
        pipe = client.pipeline()
        for row in rows:
            entity_id = str(row[view.entity])
            payload = {
                name: _encode(row.get(name))
                for name in view.features
                if row.get(name) is not None
            }
            if not payload:
                continue
            key = view.online_key(entity_id)
            pipe.hset(key, mapping=payload)
            pipe.expire(key, _TTL_SECONDS)
            written += 1
        pipe.hset(
            meta_key(view),
            mapping={
                "materialized_at": datetime.now(timezone.utc).isoformat(),
                "entities": written,
                "version": view.version,
            },
        )
        pipe.execute()
    except (redis.RedisError, OSError) as error:
        logger.warning("Ghi online store lỗi: %s", error)
        return 0
    return written


def get_online_features(view_name: str, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Đọc feature cho nhiều entity trong một round-trip. {} nếu Redis lỗi."""
    if not entity_ids:
        return {}
    view = get_view(view_name)
    client = _client()
    if client is None:
        return {}
    try:
        pipe = client.pipeline()
        for entity_id in entity_ids:
            pipe.hgetall(view.online_key(str(entity_id)))
        results = pipe.execute()
    except (redis.RedisError, OSError) as error:
        logger.warning("Đọc online store lỗi: %s", error)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entity_id, raw in zip(entity_ids, results):
        if raw:
            out[str(entity_id)] = {name: _decode(value) for name, value in raw.items()}
    return out


def feature_store_status() -> dict[str, Any]:
    """Độ tươi/độ phủ của online store — phục vụ observability."""
    client = _client()
    status: dict[str, Any] = {"online": False, "views": {}}
    if client is None:
        return status
    try:
        for name, view in REGISTRY.items():
            meta = client.hgetall(meta_key(view))
            status["views"][name] = {
                "version": view.version,
                "entity": view.entity,
                "features": list(view.features),
                "materializedAt": meta.get("materialized_at"),
                "entities": int(meta.get("entities", 0)) if meta else 0,
            }
        status["online"] = True
    except (redis.RedisError, OSError):
        return status
    return status
