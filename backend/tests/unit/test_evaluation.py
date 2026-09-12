import pytest

from app.evaluation import ndcg_at_k


def test_ndcg_is_one_for_ideal_ranking() -> None:
    relevance = {"best": 3, "good": 2, "okay": 1}

    assert ndcg_at_k(["best", "good", "okay"], relevance, 10) == pytest.approx(1.0)


def test_ndcg_penalizes_irrelevant_results_above_relevant_ones() -> None:
    relevance = {"best": 3, "good": 2}

    assert ndcg_at_k(["unknown", "good", "best"], relevance, 10) < 1.0


# --- Chỉ số bổ sung (bước A4) -------------------------------------------------
#
# Mỗi test đối chiếu với một giá trị tính TAY, không phải với chính cài đặt —
# nếu không thì test chỉ khẳng định "code làm đúng cái code đang làm".

from app.evaluation import (  # noqa: E402
    average_precision,
    map_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_reciprocal_rank_uses_first_relevant_position() -> None:
    relevance = {"a": 3, "b": 1}
    assert reciprocal_rank(["a", "x", "b"], relevance) == pytest.approx(1.0)
    assert reciprocal_rank(["x", "a"], relevance) == pytest.approx(0.5)
    assert reciprocal_rank(["x", "y", "a"], relevance) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_returned() -> None:
    assert reciprocal_rank(["x", "y"], {"a": 3}) == 0.0


def test_threshold_separates_acceptable_from_strictly_relevant() -> None:
    # 'b' chỉ ở mức 1 (chấp nhận được): tính là liên quan khi ngưỡng 1, không khi ngưỡng 2.
    relevance = {"a": 3, "b": 1}
    assert reciprocal_rank(["b", "a"], relevance, threshold=1.0) == pytest.approx(1.0)
    assert reciprocal_rank(["b", "a"], relevance, threshold=2.0) == pytest.approx(0.5)


def test_precision_divides_by_results_returned_not_by_k() -> None:
    # Trả 3 kết quả, cả 3 đều đúng, k=10 -> 1.0 chứ không phải 0.3.
    assert precision_at_k(["a", "b", "c"], {"a": 3, "b": 2, "c": 1}, k=10) == pytest.approx(1.0)
    # 2 đúng trong 4 vị trí đầu.
    assert precision_at_k(["a", "x", "b", "y"], {"a": 3, "b": 2}, k=4) == pytest.approx(0.5)


def test_recall_measures_the_retrieval_layer_not_the_ranking_layer() -> None:
    relevance = {"a": 3, "b": 2, "c": 1}
    # Tìm được 2 trong 3 tài liệu liên quan, bất kể xếp ở đâu.
    assert recall_at_k(["x", "a", "y", "b"], relevance, k=10) == pytest.approx(2 / 3)
    # Cắt ở k=2 thì chỉ còn 1 tài liệu lọt vào.
    assert recall_at_k(["x", "a", "y", "b"], relevance, k=2) == pytest.approx(1 / 3)


def test_recall_is_zero_when_no_document_is_relevant() -> None:
    assert recall_at_k(["a"], {"a": 0}, k=10) == 0.0


def test_average_precision_penalizes_relevant_results_pushed_down() -> None:
    relevance = {"a": 3, "b": 2}
    # Cả hai ở đầu: (1/1 + 2/2) / 2 = 1.0
    assert average_precision(["a", "b"], relevance, k=10) == pytest.approx(1.0)
    # Bị đẩy xuống vị trí 2 và 4: (1/2 + 2/4) / 2 = 0.5
    assert average_precision(["x", "a", "y", "b"], relevance, k=10) == pytest.approx(0.5)


def test_average_precision_punishes_missing_documents() -> None:
    # Chỉ tìm được 1 trong 2: (1/1) / 2 = 0.5, dù precision@1 là 1.0
    relevance = {"a": 3, "b": 2}
    assert average_precision(["a", "x"], relevance, k=10) == pytest.approx(0.5)
    assert precision_at_k(["a"], relevance, k=1) == pytest.approx(1.0)


def test_aggregates_average_over_queries() -> None:
    runs = [
        (["a"], {"a": 3}),  # RR = 1.0, AP = 1.0
        (["x", "b"], {"b": 3}),  # RR = 0.5, AP = 0.5
    ]
    assert mean_reciprocal_rank(runs) == pytest.approx(0.75)
    assert map_at_k(runs, k=10) == pytest.approx(0.75)
    assert mean_reciprocal_rank([]) == 0.0
    assert map_at_k([], k=10) == 0.0


@pytest.mark.parametrize("metric", [precision_at_k, recall_at_k, average_precision])
def test_metrics_reject_non_positive_k(metric) -> None:
    with pytest.raises(ValueError):
        metric(["a"], {"a": 3}, k=0)
