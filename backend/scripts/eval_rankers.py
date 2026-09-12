"""Phase 7 — so sánh Baseline (trọng số tay) với Proposed (LambdaMART).

Chạy (cần Postgres + OpenSearch, và `app/ltr/model.txt` nếu muốn đo LTR):

    python scripts/eval_rankers.py --k 10

Sinh ra ``results/eval_rankers_<ts>.{json,md}`` với đủ 5 chỉ số mà Phase 7 yêu
cầu: NDCG@K, MRR, Precision@K, Recall@K và độ trễ.

## Hai quyết định đo lường

**Truy xuất chỉ chạy MỘT LẦN cho mỗi truy vấn, dùng chung cho cả hai bộ xếp
hạng.** Nếu chạy lại thì hai bên nhận tập ứng viên khác nhau (trending đổi,
recency đổi theo từng giây) và phần chênh lệch đo được sẽ lẫn cả nhiễu truy
xuất lẫn chênh lệch thật của bộ xếp hạng.

**Độ trễ tách làm hai cột.** ``retrievalMs`` là phần dùng chung; ``rankMs`` là
phần riêng của mỗi bộ xếp hạng. Gộp chung thì chênh lệch thật của LambdaMART
(vài ms) bị chìm trong thời gian truy vấn Postgres/OpenSearch (hàng chục ms) và
bảng so sánh sẽ nói rằng "hai bên như nhau" — một kết luận sai.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from app.spatio_temporal import enrich_candidates  # noqa: E402

JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"
CANDIDATE_POOL = 100


def _prepare(case: dict) -> tuple[list[dict], float]:
    """Truy xuất + làm giàu một lần; trả (ứng viên, mili-giây)."""
    started = time.perf_counter()
    candidates, _backend = ranking.retrieve_candidates(
        case["latitude"],
        case["longitude"],
        case["radius"],
        case["query"],
        None,
        CANDIDATE_POOL,
    )
    candidates = ranking.apply_trending_boost(candidates)
    candidates = enrich_candidates(candidates)
    candidates = attach_region_ctr(candidates)
    return candidates, (time.perf_counter() - started) * 1000.0


def _rank(candidates: list[dict], ranker: str, limit: int) -> tuple[list[str], float, str]:
    # deepcopy: `rerank` ghi đè `score`/`rankerUsed` ngay trên dict ứng viên,
    # nên nếu dùng chung thì lượt đo thứ hai thấy dữ liệu lượt đầu để lại.
    working = deepcopy(candidates)
    started = time.perf_counter()
    ranked = ranking.rerank(working, ranker=ranker)
    elapsed = (time.perf_counter() - started) * 1000.0
    used = ranked[0].get("rankerUsed", ranker) if ranked else ranker
    return [item["id"] for item in ranked[:limit]], elapsed, used


def evaluate(cases: list[dict], k: int, rankers: tuple[str, ...]) -> dict[str, Any]:
    per_ranker: dict[str, dict[str, list]] = {
        name: {"runs": [], "ndcg": [], "precision": [], "recall": [], "rankMs": [], "used": set()}
        for name in rankers
    }
    retrieval_ms: list[float] = []

    for case in cases:
        candidates, prep_ms = _prepare(case)
        retrieval_ms.append(prep_ms)
        relevance = case["relevance"]
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
        latencies = sorted(bucket["rankMs"])
        results[name] = {
            "ndcg": round(statistics.fmean(bucket["ndcg"]), 4),
            "mrr": round(mean_reciprocal_rank(bucket["runs"]), 4),
            "map": round(map_at_k(bucket["runs"], k), 4),
            "precision": round(statistics.fmean(bucket["precision"]), 4),
            "recall": round(statistics.fmean(bucket["recall"]), 4),
            "rankMsMean": round(statistics.fmean(latencies), 3),
            "rankMsP95": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3),
            # Bộ xếp hạng THỰC SỰ chạy. Xin "ltr" mà không có mô hình thì đây
            # sẽ là "linear", và khi đó hai cột trong bảng là một.
            "actuallyUsed": sorted(bucket["used"]),
        }
    results["_retrieval"] = {
        "msMean": round(statistics.fmean(retrieval_ms), 3),
        "msP95": round(sorted(retrieval_ms)[min(len(retrieval_ms) - 1, int(len(retrieval_ms) * 0.95))], 3),
    }
    return results


def _markdown(results: dict[str, Any], cases: int, k: int, stamp: str) -> str:
    rankers = [name for name in results if not name.startswith("_")]
    lines = [
        f"# Phase 7 — Baseline vs Proposed ({stamp})",
        "",
        f"Đo trên {cases} truy vấn ground truth, k = {k}.",
        "",
        "| Bộ xếp hạng | nDCG@{k} | MRR | MAP@{k} | P@{k} | R@{k} | Xếp hạng (ms, TB) | Xếp hạng (ms, p95) |".replace("{k}", str(k)),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    label = {
        "linear": "Baseline — Weighted Scoring",
        "ltr": "Proposed — LightGBM LambdaMART",
    }
    for name in rankers:
        row = results[name]
        lines.append(
            f"| {label.get(name, name)} | {row['ndcg']} | {row['mrr']} | {row['map']} | "
            f"{row['precision']} | {row['recall']} | {row['rankMsMean']} | {row['rankMsP95']} |"
        )
    retrieval = results["_retrieval"]
    lines += [
        "",
        f"Truy xuất + làm giàu (dùng chung cho cả hai): {retrieval['msMean']} ms trung bình, "
        f"{retrieval['msP95']} ms p95.",
        "",
    ]

    used = {name: results[name]["actuallyUsed"] for name in rankers}
    if "ltr" in used and used["ltr"] != ["ltr"]:
        lines += [
            "> **Cảnh báo — hàng 'Proposed' KHÔNG chạy LambdaMART.**",
            f"> Bộ xếp hạng thực sự đã chạy: {used['ltr']}. Thiếu `app/ltr/model.txt`",
            "> nên hệ thống rơi về công thức tuyến tính. Hai hàng trong bảng là cùng một",
            "> thuật toán; đừng đọc chênh lệch giữa chúng như một kết quả.",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="So sánh baseline và LTR")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--judgments", type=Path, default=JUDGMENTS_PATH)
    args = parser.parse_args()

    cases = json.loads(args.judgments.read_text(encoding="utf-8"))
    cases = [case for case in cases if case.get("relevance")]
    if not cases:
        print("Không có truy vấn nào đã gán nhãn — không đo được.")
        return 1

    print(f"Trạng thái mô hình LTR: {json.dumps(ltr_model.info(), ensure_ascii=False)}")
    results = evaluate(cases, args.k, ("linear", "ltr"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"eval_rankers_{stamp}.json").write_text(
        json.dumps(
            {"cases": len(cases), "k": args.k, "results": results},
            ensure_ascii=False,
            indent=2,
            default=list,
        ),
        encoding="utf-8",
    )
    markdown = _markdown(results, len(cases), args.k, stamp)
    (RESULTS_DIR / f"eval_rankers_{stamp}.md").write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
