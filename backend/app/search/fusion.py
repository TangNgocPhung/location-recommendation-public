"""Reciprocal Rank Fusion (RRF) gộp nhiều kênh truy xuất.

RRF không cần chuẩn hóa điểm giữa các kênh vốn không cùng thang (BM25 vs cosine
vs khoảng cách): mỗi kênh chỉ đóng góp theo *thứ hạng*. Điểm của một tài liệu là
tổng ``weight / (k + rank)`` trên mọi kênh có nó, với ``rank`` bắt đầu từ 1.
"""

from __future__ import annotations

from typing import Mapping, Sequence

# Hằng số làm mượt chuẩn của RRF; k lớn thì thứ hạng đầu bảng bớt áp đảo.
DEFAULT_K = 60


def reciprocal_rank_fusion(
    channels: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    k: int = DEFAULT_K,
) -> list[tuple[str, float]]:
    """Gộp các danh sách id đã xếp hạng thành một danh sách (id, score) giảm dần.

    ``channels``: tên kênh -> danh sách poi_id theo thứ hạng.
    ``weights``:  trọng số mỗi kênh (mặc định 1.0).
    """
    weights = weights or {}
    scores: dict[str, float] = {}
    for channel, ranked_ids in channels.items():
        weight = weights.get(channel, 1.0)
        if weight == 0:
            continue
        seen: set[str] = set()
        for rank, poi_id in enumerate(ranked_ids, start=1):
            if poi_id in seen:  # một kênh không tự cộng dồn cho cùng id
                continue
            seen.add(poi_id)
            scores[poi_id] = scores.get(poi_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def fused_channels(
    channels: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    k: int = DEFAULT_K,
) -> dict[str, list[str]]:
    """Bản đồ poi_id -> danh sách kênh đã truy xuất được nó (để giải thích)."""
    membership: dict[str, list[str]] = {}
    for channel, ranked_ids in channels.items():
        for poi_id in dict.fromkeys(ranked_ids):
            membership.setdefault(poi_id, []).append(channel)
    return membership
