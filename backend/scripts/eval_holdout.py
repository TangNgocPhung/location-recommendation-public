"""Phase 9 — so sánh Linear vs LambdaMART trên nhóm truy vấn CHƯA TỪNG train.

`eval_rankers.py` (Phase 7.5) chỉ đo trên 3 truy vấn trong
`relevance_judgments.json` — quá ít để kết luận, và model.txt production đã
được train trên CHÍNH các nhóm click đó (không có gì "held-out" thật). Script
này tách nhóm theo qid TRƯỚC khi train:

    Tất cả nhóm (judgment + click)
            │
            ├── train_groups (mặc định 80%)  -> huấn luyện MỘT model tạm
            │
            └── test_groups  (mặc định 20%)  -> model tạm CHƯA THẤY nhóm này
                     │
                     ├── rerank bằng "linear" (không cần model)
                     └── rerank bằng "ltr"    (model TẠM, không phải model.txt
                                               production — xem ghi chú dưới)

Model tạm được ghi vào một thư mục KHÁC (``results/holdout_model_<ts>/``), nạp
qua biến môi trường ``LTR_MODEL_DIR`` — hoàn toàn không đụng tới
``app/ltr/model.txt`` đang phục vụ production.

Vì nhãn click chỉ nhị phân (1 = có click, 0 = còn lại) và candidate được truy
xuất LẠI qua pipeline sống (giống `eval_rankers.py`, không phải đọc lại đúng
snapshot lúc phục vụ — trending/is_open/recency đã trôi theo thời gian), số đo
ở đây có nhiễu hơn nDCG@k tính trên snapshot lúc train. Đây là hạn chế đã biết,
không phải lỗi — ghi rõ trong báo cáo.

Chạy (cần Postgres + OpenSearch):

    python scripts/eval_holdout.py --k 5 --test-fraction 0.2
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import ranking  # noqa: E402
from app.evaluation import (  # noqa: E402
    map_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from app.features.serving import attach_region_ctr  # noqa: E402
from app.ltr import model as ltr_model  # noqa: E402
from app.ltr import train as ltr_train  # noqa: E402
from app.ltr.dataset import Group, build_from_clicks, build_from_judgments, merge  # noqa: E402
from app.ltr.train import DEFAULT_PARAMS  # noqa: E402
from app.spatio_temporal import enrich_candidates  # noqa: E402

JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"
CANDIDATE_POOL = 100
MIN_TEST_GROUPS = 10  # dưới ngưỡng này, chia train/test không còn ý nghĩa thống kê


def split_groups(
    groups: list[Group], test_fraction: float, seed: int
) -> tuple[list[Group], list[Group]]:
    """Tách theo QID, không theo dòng — hai ứng viên cùng truy vấn tương quan
    mạnh, để lọt cả hai bên thì điểm đo là điểm khống (cùng lý do `train()`
    tách k-fold theo nhóm, xem docstring `app/ltr/train.py`)."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(groups))
    n_test = max(1, round(len(groups) * test_fraction))
    test_index = {int(i) for i in order[:n_test]}
    train_groups = [g for i, g in enumerate(groups) if i not in test_index]
    test_groups = [g for i, g in enumerate(groups) if i in test_index]
    return train_groups, test_groups


def _prepare(group: Group) -> tuple[list[dict], float]:
    started = time.perf_counter()
    candidates, _backend = ranking.retrieve_candidates(
        group.latitude, group.longitude, group.radius, group.query, None, CANDIDATE_POOL
    )
    candidates = ranking.apply_trending_boost(candidates)
    candidates = enrich_candidates(candidates)
    candidates = attach_region_ctr(candidates)
    return candidates, (time.perf_counter() - started) * 1000.0


def _rank(candidates: list[dict], ranker: str, limit: int) -> tuple[list[str], float, str]:
    working = deepcopy(candidates)
    started = time.perf_counter()
    ranked = ranking.rerank(working, ranker=ranker, has_query_text=True)
    elapsed = (time.perf_counter() - started) * 1000.0
    used = ranked[0].get("rankerUsed", ranker) if ranked else ranker
    return [item["id"] for item in ranked[:limit]], elapsed, used


def evaluate(test_groups: list[Group], k: int, rankers: tuple[str, ...]) -> dict[str, Any]:
    per_ranker: dict[str, dict[str, list]] = {
        name: {"runs": [], "ndcg": [], "precision": [], "recall": [], "rankMs": [], "used": set()}
        for name in rankers
    }
    retrieval_ms: list[float] = []
    skipped_no_query = 0

    for group in test_groups:
        if not group.query:
            # Duyệt theo vị trí thuần (không gõ chữ) không so được với label
            # click vốn được thu khi CÓ query — bỏ, không tính là lỗi.
            skipped_no_query += 1
            continue
        candidates, prep_ms = _prepare(group)
        if not candidates:
            continue
        retrieval_ms.append(prep_ms)
        relevance = dict(zip(group.poi_ids, group.labels))
        for name in rankers:
            ranked_ids, rank_ms, used = _rank(candidates, name, k)
            bucket = per_ranker[name]
            bucket["runs"].append((ranked_ids, relevance))
            bucket["ndcg"].append(ndcg_at_k(ranked_ids, relevance, k))
            bucket["precision"].append(precision_at_k(ranked_ids, relevance, k))
            bucket["recall"].append(recall_at_k(ranked_ids, relevance, k))
            bucket["rankMs"].append(rank_ms)
            bucket["used"].add(used)

    results: dict[str, Any] = {}
    for name, bucket in per_ranker.items():
        if not bucket["ndcg"]:
            results[name] = None
            continue
        latencies = sorted(bucket["rankMs"])
        results[name] = {
            "ndcg": round(statistics.fmean(bucket["ndcg"]), 4),
            "mrr": round(mean_reciprocal_rank(bucket["runs"]), 4),
            "map": round(map_at_k(bucket["runs"], k), 4),
            "precision": round(statistics.fmean(bucket["precision"]), 4),
            "recall": round(statistics.fmean(bucket["recall"]), 4),
            "rankMsMean": round(statistics.fmean(latencies), 3),
            "actuallyUsed": sorted(bucket["used"]),
        }
    results["_meta"] = {
        "testGroupsTotal": len(test_groups),
        "testGroupsEvaluated": len(per_ranker[rankers[0]]["ndcg"]),
        "skippedNoQuery": skipped_no_query,
        "retrievalMsMean": round(statistics.fmean(retrieval_ms), 3) if retrieval_ms else None,
    }
    return results


def _markdown(results: dict[str, Any], k: int, stamp: str, train_n: int, test_n: int) -> str:
    rankers = [name for name in results if not name.startswith("_")]
    meta = results["_meta"]
    lines = [
        f"# Phase 9 — Holdout evaluation ({stamp})",
        "",
        f"Train trên {train_n} nhóm, đánh giá trên {test_n} nhóm HOÀN TOÀN KHÔNG "
        f"nằm trong tập train (tách theo qid). {meta['testGroupsEvaluated']}/{test_n} "
        f"nhóm đánh giá được (bỏ {meta['skippedNoQuery']} nhóm không có query text).",
        "",
        "| Bộ xếp hạng | nDCG@{k} | MRR | MAP@{k} | P@{k} | R@{k} |".replace("{k}", str(k)),
        "|---|---:|---:|---:|---:|---:|",
    ]
    label = {"linear": "Baseline — Weighted Scoring", "ltr": "LambdaMART (model TẠM, chỉ train trên phần train)"}
    for name in rankers:
        row = results[name]
        if row is None:
            lines.append(f"| {label.get(name, name)} | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label.get(name, name)} | {row['ndcg']} | {row['mrr']} | {row['map']} | "
            f"{row['precision']} | {row['recall']} |"
        )
    used = {name: results[name]["actuallyUsed"] for name in rankers if results[name]}
    if "ltr" in used and used["ltr"] != ["ltr"]:
        lines += [
            "",
            "> **Cảnh báo — hàng LambdaMART KHÔNG thực sự chạy LTR.** Bộ xếp hạng "
            f"thực tế: {used['ltr']}. Kiểm tra `LTR_MODEL_DIR` / log lỗi nạp model.",
        ]
    lines += [
        "",
        "**Lưu ý quan trọng:** model LTR ở đây là model TẠM huấn luyện riêng cho lần "
        "đánh giá này (chỉ thấy `train_groups`), KHÔNG PHẢI `app/ltr/model.txt` đang "
        "chạy production (model đó train trên toàn bộ dữ liệu, không có phần "
        "held-out). Muốn dùng model đã kiểm chứng ở đây cho production thì phải "
        "train lại trên toàn bộ dữ liệu rồi rebuild backend — xem `.\\nearby.ps1 train`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 9 — đánh giá LTR trên tập test độc lập")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--radius", type=int, default=5_000)
    args = parser.parse_args()

    print("Dựng tập dữ liệu đầy đủ (judgment + click)...")
    dataset = merge(
        [
            build_from_judgments(JUDGMENTS_PATH),
            build_from_clicks(radius=args.radius),
        ]
    )
    usable = dataset.usable_groups
    print(f"Tổng {len(usable)} nhóm dùng được.")

    train_groups, test_groups = split_groups(usable, args.test_fraction, args.seed)
    print(f"Chia: {len(train_groups)} nhóm train / {len(test_groups)} nhóm test (tách theo qid).")

    if len(test_groups) < MIN_TEST_GROUPS:
        print(
            f"CẢNH BÁO: chỉ {len(test_groups)} nhóm test, dưới ngưỡng {MIN_TEST_GROUPS}. "
            "Kết quả dưới đây KHÔNG đủ để kết luận, chỉ chứng minh quy trình chạy thông."
        )
    if not train_groups:
        print("DỪNG: không còn nhóm nào để train sau khi tách test.")
        return 1

    print(f"Huấn luyện model TẠM trên {len(train_groups)} nhóm train...")
    params = {**DEFAULT_PARAMS, "ndcg_eval_at": [args.k]}
    booster = ltr_train.fit_groups(train_groups, params)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    holdout_dir = RESULTS_DIR / f"holdout_model_{stamp}"
    holdout_dir.mkdir(parents=True, exist_ok=True)
    ltr_train.write_model(booster, holdout_dir / "model.txt")
    (holdout_dir / "model.meta.json").write_text(
        json.dumps({"featureNames": list(ltr_train.FEATURE_NAMES)}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Trỏ app.ltr.model sang model TẠM này, KHÔNG đụng app/ltr/model.txt.
    old_override = os.environ.get("LTR_MODEL_DIR")
    os.environ["LTR_MODEL_DIR"] = str(holdout_dir)
    ltr_model.reset_cache()
    try:
        print(f"Trạng thái model tạm: {json.dumps(ltr_model.info(), ensure_ascii=False)}")
        print(f"Đánh giá trên {len(test_groups)} nhóm test...")
        results = evaluate(test_groups, args.k, ("linear", "ltr"))
    finally:
        if old_override is None:
            os.environ.pop("LTR_MODEL_DIR", None)
        else:
            os.environ["LTR_MODEL_DIR"] = old_override
        ltr_model.reset_cache()  # production nạp lại đúng model.txt của nó

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"eval_holdout_{stamp}.json"
    md_path = RESULTS_DIR / f"eval_holdout_{stamp}.md"
    json_path.write_text(
        json.dumps(
            {"k": args.k, "trainGroups": len(train_groups), "testGroups": len(test_groups), "results": results},
            ensure_ascii=False,
            indent=2,
            default=list,
        ),
        encoding="utf-8",
    )
    markdown = _markdown(results, args.k, stamp, len(train_groups), len(test_groups))
    md_path.write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    print(f"Báo cáo -> {json_path}\n           {md_path}")
    print(f"Model tạm (không phải production) -> {holdout_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
