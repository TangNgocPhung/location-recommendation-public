"""Kết nối OpenSearch và cổng bật/tắt truy xuất đa kênh.

``opensearch-py`` được import trễ (lazy) để các hàm thuần trong ``query`` và
``fusion`` cùng toàn bộ unit test chạy được mà không cần cài package. Mọi lỗi
kết nối được nuốt và quy về "không khả dụng" để tầng trên fallback về PostGIS.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import settings

logger = logging.getLogger("nearby-search")

# Cache trạng thái "package có cài không" để khỏi thử import lặp lại.
_CLIENT_CACHE: dict[str, Any] = {}

# Circuit breaker: sau một lần ping thất bại, coi OpenSearch là "chết" trong
# khoảng này để mỗi request kế tiếp fallback tức thì thay vì chờ timeout kết nối.
_DOWN_COOLDOWN_SECONDS = 15.0
_down_until = 0.0


def search_backend() -> str:
    return settings.search_backend


def is_search_configured() -> bool:
    """OpenSearch được cấu hình để dùng (chưa chắc đang sống)."""
    if settings.search_backend == "postgis":
        return False
    return bool(settings.opensearch_url)


def _build_client(timeout: float, retries: int) -> Any | None:
    try:
        from opensearchpy import OpenSearch
    except ImportError:
        logger.warning("opensearch-py chưa được cài; truy xuất rơi về PostGIS")
        return None
    try:
        return OpenSearch(
            hosts=[settings.opensearch_url],
            http_compress=True,
            timeout=timeout,
            max_retries=retries,
            retry_on_timeout=retries > 0,
        )
    except Exception as error:  # noqa: BLE001 - cấu hình sai không được làm sập app
        logger.warning("Không tạo được OpenSearch client: %s", error)
        return None


def get_client() -> Any | None:
    """Client cho TRUY VẤN NGƯỜI DÙNG: timeout ngắn, không thử lại.

    Ngưỡng hữu hạn là cố ý — OpenSearch chết thì thà rơi về PostGIS ngay còn hơn
    bắt người dùng chờ. Nhưng ngưỡng phải LỚN HƠN thời gian truy vấn thật, nếu
    không hệ thống rơi về PostGIS mọi lúc mà không ai biết. Xem
    `opensearch_timeout_seconds` trong config để biết số đo thực tế.

    KHÔNG dùng client này cho các thao tác quản trị chỉ mục.
    """
    if not is_search_configured():
        return None
    if "client" not in _CLIENT_CACHE:
        _CLIENT_CACHE["client"] = _build_client(
            timeout=settings.opensearch_timeout_seconds, retries=0
        )
    return _CLIENT_CACHE["client"]


def get_admin_client() -> Any | None:
    """Client cho JOB INDEXING: timeout dài, có thử lại.

    Xoá/tạo chỉ mục và bulk index 3.010 POI không thể xong trong 1 giây. Dùng
    nhầm client truy vấn ở đây làm job indexing đổ giữa chừng vì timeout, và đó
    là lỗi rất khó đoán ra vì thông báo lỗi không hề nhắc tới timeout.
    """
    if not is_search_configured():
        return None
    if "admin_client" not in _CLIENT_CACHE:
        _CLIENT_CACHE["admin_client"] = _build_client(timeout=60, retries=3)
    return _CLIENT_CACHE["admin_client"]


def ping(client: Any | None = None) -> bool:
    """OpenSearch có đang sống và trả lời không."""
    client = client or get_client()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:  # noqa: BLE001
        return False


def search_available() -> bool:
    """Cổng chính cho tầng truy xuất, có circuit breaker.

    Trả về False tức thì nếu chưa cấu hình hoặc đang trong cửa sổ cooldown sau
    một lần ping thất bại. Ngược lại ping thật một lần; nếu chết thì mở cooldown
    và buộc tạo lại client lần sau. Nhờ vậy khi OpenSearch sập, chỉ request đầu
    tiên phải chờ (1 lần, timeout 1s), các request trong 15s kế tiếp fallback
    tức thì.
    """
    global _down_until
    if not is_search_configured():
        return False
    now = time.monotonic()
    if now < _down_until:
        return False
    client = get_client()
    if client is not None and ping(client):
        return True
    _down_until = now + _DOWN_COOLDOWN_SECONDS
    _CLIENT_CACHE.pop("client", None)  # client có thể đã hỏng, tạo lại lần sau
    return False


def reset_client_cache() -> None:
    """Dùng trong test hoặc khi đổi cấu hình runtime."""
    global _down_until
    _CLIENT_CACHE.clear()
    _down_until = 0.0
