"""Đo chất lượng xếp hạng trên tập ground truth và LƯU kết quả ra file.

Chạy:
    API_BASE_URL=http://localhost:8000 python scripts/evaluate_relevance.py

Kết quả ghi vào `backend/results/relevance_<timestamp>.json`. Ghi ra file là bắt
buộc cho chương Thực nghiệm: bảng số liệu trong báo cáo phải truy ngược được về
một lần chạy cụ thể, kèm mốc thời gian và backend truy xuất đã dùng.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation import (  # noqa: E402
    average_precision,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
MIN_NDCG_AT_10 = float(os.getenv("MIN_NDCG_AT_10", "0.75"))
EVAL_K = int(os.getenv("EVAL_K", "10"))
RELEVANT_THRESHOLD = float(os.getenv("RELEVANT_THRESHOLD", "1.0"))
JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def main() -> None:
    judgments = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    cases: list[dict] = []
    backends: set[str] = set()

    with httpx.Client(base_url=API_BASE_URL, timeout=15) as client:
        for case in judgments:
            response = client.post(
                "/api/v1/search",
                json={
                    "query": case["query"],
                    "latitude": case["latitude"],
                    "longitude": case["longitude"],
                    "radius": case["radius"],
                    "limit": EVAL_K,
                },
            )
            response.raise_for_status()
            payload = response.json()
            ranked_ids = [poi["id"] for poi in payload["results"]]
            # Ghi lại backend thật sự phục vụ truy vấn: nếu là "postgis" thì số
            # đo được là của đường dự phòng, không phải của truy xuất đa kênh.
            backends.add(payload.get("retrievalBackend", "unknown"))
            relevance = case["relevance"]
            cases.append(
                {
                    "query": case["query"],
                    # Nhóm truy vấn (ngắn / có địa danh / không dấu / sai chính tả
                    # / theo giờ) để phân tích được điểm mạnh yếu theo từng loại.
                    "group": case.get("group", "chưa phân nhóm"),
                    "returned": len(ranked_ids),
                    "ndcg": round(ndcg_at_k(ranked_ids, relevance, EVAL_K), 4),
                    "mrr": round(reciprocal_rank(ranked_ids, relevance, RELEVANT_THRESHOLD), 4),
                    "precision": round(
                        precision_at_k(ranked_ids, relevance, EVAL_K, RELEVANT_THRESHOLD), 4
                    ),
                    "recall": round(
                        recall_at_k(ranked_ids, relevance, EVAL_K, RELEVANT_THRESHOLD), 4
                    ),
                    "ap": round(
                        average_precision(ranked_ids, relevance, EVAL_K, RELEVANT_THRESHOLD), 4
                    ),
                }
            )

    overall = {
        "ndcg": round(_mean([c["ndcg"] for c in cases]), 4),
        "mrr": round(_mean([c["mrr"] for c in cases]), 4),
        "precision": round(_mean([c["precision"] for c in cases]), 4),
        "recall": round(_mean([c["recall"] for c in cases]), 4),
        "map": round(_mean([c["ap"] for c in cases]), 4),
    }

    by_group: dict[str, dict] = {}
    for case in cases:
        bucket = by_group.setdefault(case["group"], {"queries": 0, "ndcg": [], "mrr": [], "recall": []})
        bucket["queries"] += 1
        for metric in ("ndcg", "mrr", "recall"):
            bucket[metric].append(case[metric])
    for bucket in by_group.values():
        for metric in ("ndcg", "mrr", "recall"):
            bucket[metric] = round(_mean(bucket[metric]), 4)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "measuredAt": stamp,
        "apiBaseUrl": API_BASE_URL,
        "k": EVAL_K,
        "relevantThreshold": RELEVANT_THRESHOLD,
        "queries": len(cases),
        "retrievalBackends": sorted(backends),
        "overall": overall,
        "byGroup": by_group,
        "cases": cases,
        "thresholds": {"min_ndcg_at_10": MIN_NDCG_AT_10},
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"relevance_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for case in cases:
        print(
            f"query={case['query']!r:28} ndcg={case['ndcg']:.4f} mrr={case['mrr']:.4f} "
            f"P={case['precision']:.4f} R={case['recall']:.4f}"
        )
    print(f"\n{len(cases)} truy vấn · backend={sorted(backends)}")
    print(
        f"mean ndcg@{EVAL_K}={overall['ndcg']:.4f} MRR={overall['mrr']:.4f} "
        f"P@{EVAL_K}={overall['precision']:.4f} R@{EVAL_K}={overall['recall']:.4f} "
        f"MAP@{EVAL_K}={overall['map']:.4f}"
    )
    print(f"đã ghi {out.relative_to(PROJECT_ROOT)}")

    if backends and backends != {"opensearch"}:
        # Cảnh báo chứ không chặn: chạy local không có OpenSearch vẫn cần đo được.
        print(f"CẢNH BÁO: đang đo đường dự phòng, backend={sorted(backends)}", file=sys.stderr)
    if overall["ndcg"] < MIN_NDCG_AT_10:
        print(f"FAIL: ndcg {overall['ndcg']:.4f} < ngưỡng {MIN_NDCG_AT_10:.4f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
