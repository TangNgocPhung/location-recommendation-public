"""Chỉ số đánh giá chất lượng xếp hạng và truy xuất.

Thuần dữ liệu, không I/O, để cả script đo lẫn unit test dùng chung một cài đặt.

Nhãn liên quan dùng thang 4 mức (xem `docs/quality-gates.md`):
3 = hoàn hảo, 2 = liên quan, 1 = chấp nhận được, 0 = không liên quan.

nDCG dùng trực tiếp thang này. Các chỉ số nhị phân (MRR, precision, recall, AP)
cần một ngưỡng để quy về "liên quan / không liên quan"; mặc định lấy `>= 1.0`,
tức mọi mức từ "chấp nhận được" trở lên. Ngưỡng để lộ ra thành tham số vì báo
cáo thường cần cả hai cách đọc: nới (>=1) và chặt (>=2).
"""

import math
from collections.abc import Mapping, Sequence


DEFAULT_RELEVANT_THRESHOLD = 1.0


def _is_relevant(
    item_id: str, relevance: Mapping[str, float], threshold: float
) -> bool:
    return float(relevance.get(item_id, 0.0)) >= threshold


def _check_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be positive")


def ndcg_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, float], k: int = 10) -> float:
    """Normalized discounted cumulative gain cho một danh sách kết quả đã xếp hạng."""
    _check_k(k)

    def dcg(grades: Sequence[float]) -> float:
        return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))

    actual = [float(relevance.get(item_id, 0.0)) for item_id in ranked_ids[:k]]
    ideal = sorted((float(grade) for grade in relevance.values()), reverse=True)[:k]
    ideal_score = dcg(ideal)
    return dcg(actual) / ideal_score if ideal_score else 0.0


def reciprocal_rank(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float],
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """1/vị trí của kết quả liên quan ĐẦU TIÊN; 0 nếu không có kết quả nào liên quan.

    Trung bình trên nhiều truy vấn cho MRR. Chỉ số này hợp với bài toán tìm địa
    điểm hơn nDCG ở chỗ nó phản ánh đúng hành vi thực tế: người dùng thường chỉ
    bấm vào kết quả đầu tiên trông hợp lý rồi rời đi.
    """
    for index, item_id in enumerate(ranked_ids):
        if _is_relevant(item_id, relevance, threshold):
            return 1.0 / (index + 1)
    return 0.0


def precision_at_k(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float],
    k: int = 10,
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """Tỉ lệ kết quả liên quan trong k vị trí đầu.

    Mẫu số là số kết quả THỰC SỰ trả về (tối đa k), không phải k: nếu hệ thống
    chỉ trả 3 kết quả và cả 3 đều đúng thì precision là 1.0, không phải 0.3.
    """
    _check_k(k)
    top = ranked_ids[:k]
    if not top:
        return 0.0
    hits = sum(1 for item_id in top if _is_relevant(item_id, relevance, threshold))
    return hits / len(top)


def recall_at_k(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float],
    k: int = 10,
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """Tỉ lệ tài liệu liên quan được tìm thấy trong k vị trí đầu.

    Đây là chỉ số đo TẦNG TRUY XUẤT, tách bạch khỏi tầng xếp hạng: nếu recall
    của truy xuất thấp thì mô hình xếp hạng có tốt đến mấy cũng vô ích, vì kết
    quả đúng chưa từng lọt vào tập ứng viên. Tách hai tầng như vậy mới chỉ ra
    được nên đầu tư công sức vào đâu.
    """
    _check_k(k)
    total_relevant = sum(
        1 for grade in relevance.values() if float(grade) >= threshold
    )
    if not total_relevant:
        return 0.0
    hits = sum(
        1 for item_id in ranked_ids[:k] if _is_relevant(item_id, relevance, threshold)
    )
    return hits / total_relevant


def average_precision(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float],
    k: int = 10,
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """Trung bình precision tại mỗi vị trí có kết quả liên quan.

    Chia cho tổng số tài liệu liên quan (giới hạn ở k), nên một truy vấn bỏ sót
    kết quả đúng sẽ bị phạt — khác precision@k vốn không biết mình đã bỏ sót gì.
    """
    _check_k(k)
    total_relevant = min(
        k, sum(1 for grade in relevance.values() if float(grade) >= threshold)
    )
    if not total_relevant:
        return 0.0
    hits = 0
    running = 0.0
    for index, item_id in enumerate(ranked_ids[:k]):
        if _is_relevant(item_id, relevance, threshold):
            hits += 1
            running += hits / (index + 1)
    return running / total_relevant


def map_at_k(
    runs: Sequence[tuple[Sequence[str], Mapping[str, float]]],
    k: int = 10,
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """Mean Average Precision trên nhiều truy vấn."""
    if not runs:
        return 0.0
    return sum(average_precision(ids, rel, k, threshold) for ids, rel in runs) / len(runs)


def mean_reciprocal_rank(
    runs: Sequence[tuple[Sequence[str], Mapping[str, float]]],
    threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> float:
    """MRR trên nhiều truy vấn."""
    if not runs:
        return 0.0
    return sum(reciprocal_rank(ids, rel, threshold) for ids, rel in runs) / len(runs)
