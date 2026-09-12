"""Phase 5 — huấn luyện LightGBM LambdaMART.

``LGBMRanker(objective="lambdarank")`` là hiện thực LambdaMART: cây tăng cường
gradient, trong đó gradient được nhân với mức thay đổi nDCG khi hoán vị một cặp
tài liệu. Nhờ đó mô hình tối ưu trực tiếp chỉ số xếp hạng thay vì tối ưu sai số
điểm số của từng tài liệu riêng lẻ [Burges 2010].

## Tách tập theo NHÓM, không theo dòng

Hai ứng viên của cùng một truy vấn có tương quan rất mạnh. Tách ngẫu nhiên theo
dòng sẽ để cùng một truy vấn xuất hiện ở cả tập huấn luyện lẫn tập kiểm tra, và
điểm nDCG thu được là điểm khống. Ở đây luôn tách theo ``qid``.

## Dữ liệu nhỏ thì nói thẳng

Với vài chục nhóm, một lần tách train/test cho ra con số dao động cực lớn. Hàm
``train()`` vì vậy chạy k-fold theo nhóm và báo cả trung bình lẫn độ lệch
chuẩn. Dưới ``MIN_GROUPS_FOR_CLAIM`` nhóm, kết quả được đánh dấu
``"insufficientData": true`` và KHÔNG được dùng để kết luận mô hình tốt hơn
baseline — nó chỉ chứng minh đường ống chạy thông.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..evaluation import ndcg_at_k
from .dataset import Dataset, Group
from .features import FEATURE_NAMES

logger = logging.getLogger("nearby-ltr")

# Dưới ngưỡng này thì mọi so sánh với baseline đều là nhiễu. Con số không có
# gì thiêng liêng; nó phản ánh thực tế là lambdarank cần đủ nhóm để ước lượng
# được mức thay đổi nDCG một cách ổn định.
MIN_GROUPS_FOR_CLAIM = 30

# API GỐC của LightGBM (`lgb.train`), không phải API sklearn (`LGBMRanker`).
# Lý do là phụ thuộc: `lightgbm.sklearn` bắt buộc phải có scikit-learn, mà ảnh
# phục vụ chỉ cần `lgb.Booster` để nạp mô hình. Dùng API gốc thì image không
# phải kéo theo scikit-learn chỉ để chấm điểm.
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "boosting_type": "gbdt",
    # Tập huấn luyện nhỏ: cây nông, ít lá, learning rate thấp và nhiều vòng.
    # Để mặc định (31 lá) thì mô hình nhớ thuộc lòng vài chục nhóm.
    "num_leaves": 7,
    "max_depth": 3,
    "learning_rate": 0.05,
    "min_data_in_leaf": 5,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "lambda_l2": 1.0,
    "label_gain": [0, 1, 3, 7],  # khop thang nhan 0-3 cua quality-gates
    "verbose": -1,
}

NUM_BOOST_ROUND = 200


def _matrices(groups: list[Group]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    features = np.array(
        [row for group in groups for row in group.rows], dtype=np.float64
    )
    labels = np.array(
        [label for group in groups for label in group.labels], dtype=np.float64
    )
    sizes = np.array([len(group) for group in groups], dtype=np.int64)
    return features, labels, sizes


def _fold_ndcg(model: Any, groups: list[Group], k: int) -> float:
    """nDCG@k trung bình trên các nhóm, dùng đúng hàm đo của `evaluation`."""
    scores: list[float] = []
    for group in groups:
        if group.n_positive == 0:
            continue
        predictions = model.predict(np.array(group.rows, dtype=np.float64))
        order = np.argsort(-predictions)
        ranked_ids = [group.poi_ids[i] for i in order]
        relevance = dict(zip(group.poi_ids, group.labels))
        scores.append(ndcg_at_k(ranked_ids, relevance, k))
    return float(np.mean(scores)) if scores else 0.0


def train(
    dataset: Dataset,
    k: int = 10,
    folds: int = 5,
    params: dict[str, Any] | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Huấn luyện LambdaMART và trả báo cáo kèm mô hình cuối.

    Trả về dict có khóa ``model`` (đối tượng LGBMRanker đã fit trên TOÀN BỘ dữ
    liệu) và các khóa số liệu. Việc ghi ra đĩa do ``save()`` đảm nhiệm.
    """
    import lightgbm as lgb  # import muộn: chỉ script huấn luyện mới cần

    usable = dataset.usable_groups
    if not usable:
        raise ValueError(
            "Không có nhóm nào dùng được: mọi truy vấn đều không có ứng viên "
            "liên quan. Cần gán nhãn (judgment) hoặc thu thập click trước."
        )

    params = {**DEFAULT_PARAMS, **(params or {})}
    rounds = int(params.pop("num_boost_round", NUM_BOOST_ROUND))
    params["ndcg_eval_at"] = [k]

    def _fit(groups: list[Group]) -> Any:
        features, labels, sizes = _matrices(groups)
        train_set = lgb.Dataset(
            features,
            label=labels,
            group=sizes,
            feature_name=list(FEATURE_NAMES),
            free_raw_data=False,
        )
        return lgb.train(params, train_set, num_boost_round=rounds)

    rng = np.random.default_rng(seed)
    order = rng.permutation(len(usable))
    n_folds = max(2, min(folds, len(usable)))

    fold_scores: list[float] = []
    for fold in range(n_folds):
        test_index = {int(i) for i in order[fold::n_folds]}
        train_groups = [g for i, g in enumerate(usable) if i not in test_index]
        test_groups = [g for i, g in enumerate(usable) if i in test_index]
        if not train_groups or not test_groups:
            continue
        fold_scores.append(_fold_ndcg(_fit(train_groups), test_groups, k))

    # Mô hình giao đi là mô hình fit trên toàn bộ dữ liệu; số liệu báo cáo là
    # của k-fold ở trên, không phải của mô hình này trên chính dữ liệu nó thấy.
    final = _fit(usable)
    _, _, sizes_all = _matrices(usable)

    importance = dict(
        zip(FEATURE_NAMES, (int(v) for v in final.feature_importance("gain")))
    )
    return {
        "model": final,
        "params": params,
        "k": k,
        "folds": n_folds,
        "groups": len(usable),
        "rows": int(sizes_all.sum()),
        "positives": sum(group.n_positive for group in usable),
        "ndcgMean": round(float(np.mean(fold_scores)), 4) if fold_scores else None,
        "ndcgStd": round(float(np.std(fold_scores)), 4) if fold_scores else None,
        "ndcgPerFold": [round(score, 4) for score in fold_scores],
        "featureImportance": dict(
            sorted(importance.items(), key=lambda item: -item[1])
        ),
        "insufficientData": len(usable) < MIN_GROUPS_FOR_CLAIM,
        "minGroupsForClaim": MIN_GROUPS_FOR_CLAIM,
    }


def save(report: dict[str, Any], directory: Path) -> dict[str, Path]:
    """Ghi mô hình + siêu dữ liệu. Danh sách đặc trưng lưu KÈM mô hình để
    ``model.load()`` từ chối nạp khi `features.py` đã đổi."""
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / "model.txt"
    meta_path = directory / "model.meta.json"

    report["model"].save_model(str(model_path))
    meta = {key: value for key, value in report.items() if key != "model"}
    meta["featureNames"] = list(FEATURE_NAMES)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"model": model_path, "meta": meta_path}


def dataset_report(dataset: Dataset) -> dict[str, Any]:
    """Tóm tắt tập dữ liệu kèm 20 nhóm bị bỏ đầu tiên, để biết vì sao bị bỏ."""
    return {**dataset.summary(), "skippedDetail": dataset.skipped[:20]}
