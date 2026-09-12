"""Đọc feature lúc serving. Luôn có fallback: nếu online store rỗng/lỗi thì
tầng gọi tự tính trực tiếp từ Postgres như trước."""

from __future__ import annotations

from typing import Any

from .online import get_online_features
from .registry import region_key


def attach_region_ctr(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gắn ``regionCtr`` (CTR lịch sử của cặp quận×category) vào từng candidate."""
    if not candidates:
        return candidates
    keys = {region_key(c.get("district"), c.get("category")) for c in candidates}
    features = get_online_features("region_ctr", sorted(keys))
    for candidate in candidates:
        row = features.get(region_key(candidate.get("district"), candidate.get("category")))
        try:
            # Kẹp lại ngay cả khi offline store đã kẹp: Redis giữ giá trị từ lần
            # materialize TRƯỚC, nên các giá trị hỏng (>1.0) sinh ra bởi công
            # thức cũ vẫn nằm đó cho tới lần materialize kế tiếp. Đây là lớp
            # phòng thủ cuối ngay trước công thức xếp hạng.
            raw = float(row["ctr"]) if row and "ctr" in row else 0.0
            candidate["regionCtr"] = min(1.0, max(0.0, raw))
        except (TypeError, ValueError):
            candidate["regionCtr"] = 0.0
    return candidates


def session_profile(session_id: str | None) -> dict[str, Any] | None:
    """Vector hồ sơ người dùng từ online store, hoặc None nếu chưa materialize."""
    if not session_id:
        return None
    features = get_online_features("user_profile", [str(session_id)])
    return features.get(str(session_id))


def profile_category_boost(profile: dict[str, Any] | None, scale: float = 0.2) -> dict[str, float]:
    """Đổi affinity đã chuẩn hóa [0,1] trong feature store thành category_boost
    cùng thang với ``ranking.fetch_category_affinity`` (tối đa 0.2)."""
    if not profile:
        return {}
    affinity = profile.get("affinity")
    if not isinstance(affinity, dict) or not affinity:
        return {}
    boost: dict[str, float] = {}
    for category, weight in affinity.items():
        try:
            boost[category] = round(scale * float(weight), 6)
        except (TypeError, ValueError):
            continue
    return boost
