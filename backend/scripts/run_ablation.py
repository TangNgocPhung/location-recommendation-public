"""Ablation study + so sánh baseline cho chương Thực nghiệm.

Chạy (cần Postgres/OpenSearch đang chạy, thường là bên trong container backend):
    python scripts/run_ablation.py

Sinh ra:
  - results/ablation_<timestamp>.json  — số liệu thô
  - results/ablation_<timestamp>.md    — hai bảng Markdown dán thẳng vào báo cáo
  - results/ablation_<timestamp>_configs.svg và _signals.svg — hai biểu đồ cột

Trả lời hai câu hỏi mà hội đồng gần như chắc chắn sẽ hỏi:

1. "So với cái gì mà nói là tốt?" — Bảng 1 đặt hệ thống đầy đủ cạnh ba cấu hình
   rút gọn dần, tới tận baseline PostGIS thuần.
2. "Vì sao w_text = 0.26?" — Bảng 2 tắt lần lượt từng tín hiệu và đo nDCG tụt bao
   nhiêu. Tín hiệu nào tắt đi mà điểm không đổi thì trọng số của nó chưa có căn cứ.

Script gọi THẲNG các hàm trong pipeline thay vì qua HTTP, vì cấu hình B0/B1 và
việc tắt từng trọng số đòi hỏi thay đổi tham số ở giữa pipeline — qua HTTP thì
phải khởi động lại server cho mỗi cấu hình.

Biểu đồ sinh bằng SVG thuần, không thêm phụ thuộc: matplotlib không có trong
requirements và không đáng thêm chỉ để vẽ hai biểu đồ cột.
"""

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import ranking  # noqa: E402
from app.config import settings  # noqa: E402
from app.evaluation import (  # noqa: E402
    map_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from app.search.retrieval import multi_channel_candidates  # noqa: E402
from app.spatio_temporal import enrich_candidates  # noqa: E402

EVAL_K = int(os.getenv("EVAL_K", "10"))
JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"
CANDIDATE_POOL = 100
# Script này đo trên pipeline SỐNG (enrich_candidates/apply_trending_boost tính
# lại recency/trending/popularity tại đúng thời điểm gọi), không phải feature
# đã đóng băng như ranking_snapshots — nên một lần chạy duy nhất có thể lệch
# nếu cửa sổ trending (15p/1h/24h) trong Redis vừa trôi đúng lúc đo (đã đo được
# thật: cùng máy, cách nhau vài phút, nDCG bậc D lệch 0.41 -> 0.71). Lặp N lần
# LIÊN TIẾP (không sinh traffic xen giữa) và báo mean±std thay vì một con số.
DEFAULT_REPEATS = int(os.getenv("ABLATION_REPEATS", "5"))


def _mean_std(values: list[float]) -> tuple[float, float]:
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return round(mean, 4), round(std, 4)


def _retrieve(case: dict, mode: str) -> list[dict]:
    """Lấy ứng viên theo từng cấu hình truy xuất."""
    args = (
        case["latitude"],
        case["longitude"],
        case["radius"],
        case["query"],
        None,
        CANDIDATE_POOL,
    )
    if mode == "postgis":
        return ranking.fetch_candidates(*args)
    if mode == "no_knn":
        # Tắt kênh vector, giữ BM25 + geo. settings là đối tượng dùng chung nên
        # phải trả lại giá trị cũ, nếu không cấu hình chạy sau bị nhiễm.
        previous = settings.opensearch_knn_enabled
        try:
            settings.opensearch_knn_enabled = False
            candidates = multi_channel_candidates(*args)
        finally:
            settings.opensearch_knn_enabled = previous
        return candidates if candidates is not None else ranking.fetch_candidates(*args)
    candidates = multi_channel_candidates(*args)
    return candidates if candidates is not None else ranking.fetch_candidates(*args)


def _rank(
    case: dict, mode: str, weights: dict, enrich: bool, ranker: str = "linear"
) -> tuple[list[str], float, str]:
    """Tra (top-k id, mili-giay toan bo, bo xep hang thuc su da chay)."""
    started = time.perf_counter()
    candidates = _retrieve(case, mode)
    if not candidates:
        return [], (time.perf_counter() - started) * 1000.0, ranker
    candidates = ranking.apply_trending_boost(candidates)
    if enrich:
        candidates = enrich_candidates(candidates)
        candidates = ranking.attach_region_ctr(candidates)
    else:
        # Khong lam giau ngu canh: cac tin hieu tuong ung phai VANG MAT that,
        # chu khong phai bang 0 do trong so - hai chuyen khac nhau.
        for candidate in candidates:
            candidate.pop("recencyScore", None)
            candidate.pop("contextScore", None)
            candidate.pop("regionCtr", None)
    candidates = ranking.rerank(candidates, weights=weights, ranker=ranker)
    candidates = ranking.diversify(candidates)
    used = candidates[0].get("rankerUsed", ranker) if candidates else ranker
    elapsed = (time.perf_counter() - started) * 1000.0
    return [candidate["id"] for candidate in candidates[:EVAL_K]], elapsed, used


def _measure(
    cases: list[dict], mode: str, weights: dict, enrich: bool, ranker: str = "linear"
) -> dict:
    runs = []
    ndcgs = []
    precisions = []
    latencies = []
    used_rankers = set()
    for case in cases:
        ranked, elapsed, used = _rank(case, mode, weights, enrich, ranker)
        runs.append((ranked, case["relevance"]))
        ndcgs.append(ndcg_at_k(ranked, case["relevance"], EVAL_K))
        precisions.append(precision_at_k(ranked, case["relevance"], EVAL_K))
        latencies.append(elapsed)
        used_rankers.add(used)
    if not runs:
        return {
            "ndcg": 0.0, "mrr": 0.0, "map": 0.0, "recall": 0.0,
            "precision": 0.0, "latencyMs": 0.0, "latencyP95Ms": 0.0,
            "rankerUsed": [ranker],
        }
    ordered = sorted(latencies)
    return {
        "ndcg": round(sum(ndcgs) / len(ndcgs), 4),
        "mrr": round(mean_reciprocal_rank(runs), 4),
        "map": round(map_at_k(runs, EVAL_K), 4),
        "recall": round(
            sum(recall_at_k(ids, rel, EVAL_K) for ids, rel in runs) / len(runs), 4
        ),
        "precision": round(sum(precisions) / len(precisions), 4),
        "latencyMs": round(sum(latencies) / len(latencies), 1),
        "latencyP95Ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        # Bac E xin "ltr"; thieu model.txt thi no am tham thanh "linear" va
        # bang so sanh se noi doi neu khong ghi lai dieu nay.
        "rankerUsed": sorted(used_rankers),
    }


def _svg_bars(title: str, labels: list[str], values: list[float], path: Path) -> None:
    """Biểu đồ cột tối giản bằng SVG thuần."""
    width, height = 900, 420
    left, bottom, top = 70, 90, 50
    plot_w = width - left - 30
    plot_h = height - bottom - top
    peak = max(values + [0.001])
    slot = plot_w / max(len(values), 1)
    bar_w = slot * 0.62
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {} {}" '
        'font-family="system-ui, sans-serif" font-size="13">'.format(width, height),
        '<text x="{}" y="28" text-anchor="middle" font-size="17" font-weight="600">'
        "{}</text>".format(width / 2, title),
    ]
    for tick in range(5):
        value = peak * tick / 4
        y = top + plot_h - (value / peak) * plot_h
        parts.append(
            '<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#d8dee6" '
            'stroke-width="1"/>'.format(left, y, left + plot_w, y)
        )
        parts.append(
            '<text x="{}" y="{:.1f}" text-anchor="end" fill="#5b6673">{:.2f}</text>'.format(
                left - 10, y + 4, value
            )
        )
    for index, (label, value) in enumerate(zip(labels, values)):
        bar_h = (value / peak) * plot_h
        x = left + index * slot + (slot - bar_w) / 2
        y = top + plot_h - bar_h
        parts.append(
            '<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" fill="#0f8a62" '
            'rx="3"/>'.format(x, y, bar_w, bar_h)
        )
        parts.append(
            '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" font-weight="600">'
            "{:.3f}</text>".format(x + bar_w / 2, y - 7, value)
        )
        parts.append(
            '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" fill="#3d4753">{}</text>'.format(
                x + bar_w / 2, top + plot_h + 20, label
            )
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    cases = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    full = dict(ranking.DEFAULT_WEIGHTS)
    repeats = DEFAULT_REPEATS

    # --- Bang 1: thang cau hinh cong don (Phase 8) ---
    #
    # Moi bac them DUNG MOT thu so voi bac truoc, nen chenh lech giua hai dong
    # lien tiep quy duoc cho thanh phan vua them. Bang B0-B3 truoc day tron
    # nhieu chieu cung luc (doi ca truy xuat lan trong so) nen khong quy trach
    # nhiem duoc cho thanh phan nao.
    text_spatial_only = {
        **{key: 0.0 for key in ranking.DEFAULT_WEIGHTS},
        "text": ranking.DEFAULT_WEIGHTS["text"],
        "spatial": ranking.DEFAULT_WEIGHTS["spatial"],
    }
    configs = [
        ("A", "PostGIS thuan (pg_trgm + ST_DWithin)", "postgis", text_spatial_only, False, "linear"),
        ("B", "+ OpenSearch BM25 + geo (tat kenh vector)", "no_knn", text_spatial_only, False, "linear"),
        ("C", "+ RRF 3 kenh (them kenh vector, hop nhat thu hang)", "full", text_spatial_only, False, "linear"),
        ("D", "+ Spatio-Temporal + du 9 tin hieu co trong so", "full", full, True, "linear"),
        ("E", "+ LTR (LightGBM LambdaMART)", "full", full, True, "ltr"),
    ]

    # Gom theo LAP: trong CÙNG một lap, bậc D và các tín hiệu bị tắt được đo
    # gần nhau về thời gian nhất có thể, nên delta = (tắt tín hiệu) - (bậc D
    # CỦA CHÍNH LAP ĐÓ) — không phải trừ vào baseline trung bình của các lap
    # khác — mới kiểm soát được phần trôi do trending/popularity sống thay đổi
    # giữa các lap (xem ghi chú DEFAULT_REPEATS ở đầu file).
    table1_runs: dict[str, list[dict]] = {key: [] for key, *_ in configs}
    table2_ndcg: dict[str, list[float]] = {signal: [] for signal in full}
    table2_delta: dict[str, list[float]] = {signal: [] for signal in full}

    for lap in range(1, repeats + 1):
        print("--- lap {}/{} ---".format(lap, repeats))
        lap_d_ndcg = None
        for key, description, mode, weights, enrich, ranker in configs:
            metrics = _measure(cases, mode, weights, enrich, ranker)
            table1_runs[key].append(metrics)
            if key == "D":
                lap_d_ndcg = metrics["ndcg"]
            print(
                "  {}  ndcg={:.4f}  mrr={:.4f}  recall={:.4f}  {:.1f}ms  {}".format(
                    key, metrics["ndcg"], metrics["mrr"], metrics["recall"],
                    metrics["latencyMs"], description,
                )
            )
        for signal in full:
            metrics = _measure(cases, "full", {**full, signal: 0.0}, True)
            delta = round(metrics["ndcg"] - lap_d_ndcg, 4)
            table2_ndcg[signal].append(metrics["ndcg"])
            table2_delta[signal].append(delta)
            print("  tắt {:11} ndcg={:.4f}  delta={:+.4f}".format(signal, metrics["ndcg"], delta))

    table1 = []
    for key, description, mode, weights, enrich, ranker in configs:
        runs = table1_runs[key]
        ndcg_mean, ndcg_std = _mean_std([r["ndcg"] for r in runs])
        mrr_mean, _ = _mean_std([r["mrr"] for r in runs])
        map_mean, _ = _mean_std([r["map"] for r in runs])
        precision_mean, _ = _mean_std([r["precision"] for r in runs])
        recall_mean, _ = _mean_std([r["recall"] for r in runs])
        latency_mean, _ = _mean_std([r["latencyMs"] for r in runs])
        used_rankers = sorted(set().union(*(set(r["rankerUsed"]) for r in runs)))
        table1.append(
            {
                "config": key,
                "description": description,
                "ndcgMean": ndcg_mean,
                "ndcgStd": ndcg_std,
                "mrrMean": mrr_mean,
                "mapMean": map_mean,
                "precisionMean": precision_mean,
                "recallMean": recall_mean,
                "latencyMsMean": latency_mean,
                "rankerUsed": used_rankers,
            }
        )

    table2 = []
    for signal in full:
        ndcg_mean, ndcg_std = _mean_std(table2_ndcg[signal])
        delta_mean, delta_std = _mean_std(table2_delta[signal])
        table2.append(
            {
                "signal": signal,
                "weight": full[signal],
                "ndcgWithoutMean": ndcg_mean,
                "ndcgWithoutStd": ndcg_std,
                "deltaMean": delta_mean,
                "deltaStd": delta_std,
            }
        )
    table2.sort(key=lambda row: row["deltaMean"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(exist_ok=True)
    report = {
        "measuredAt": stamp,
        "k": EVAL_K,
        "queries": len(cases),
        "repeats": repeats,
        "configs": table1,
        "signalAblation": table2,
    }
    (RESULTS_DIR / "ablation_{}.json".format(stamp)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Ablation study — {}".format(stamp),
        "",
        "Đo trên {} truy vấn ground truth, k = {}, lặp {} lần liên tiếp (không "
        "sinh traffic xen giữa) — số liệu dưới đây là mean ± std qua {} lần đo, "
        "không phải một lần chạy đơn lẻ. Script này dùng pipeline SỐNG (không "
        "phải feature đã snapshot như `eval_holdout.py`), nên vẫn có thể lệch "
        "nếu so với một phiên đo khác cách xa về thời gian — xem ghi chú "
        "`DEFAULT_REPEATS` trong `run_ablation.py`.".format(
            len(cases), EVAL_K, repeats, repeats
        ),
        "",
        "## Bảng 1 — So sánh cấu hình",
        "",
        "| Bậc | Mô tả | nDCG@10 (mean±std) | MRR | MAP@10 | P@10 | Recall@10 | Độ trễ TB (ms) |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in table1:
        lines.append(
            "| {} | {} | {:.4f} ± {:.4f} | {:.4f} | {:.4f} | {:.4f} | {:.4f} | {:.1f} |".format(
                row["config"], row["description"], row["ndcgMean"], row["ndcgStd"],
                row["mrrMean"], row["mapMean"], row["precisionMean"], row["recallMean"],
                row["latencyMsMean"],
            )
        )
    rung_e = next((row for row in table1 if row["config"] == "E"), None)
    if rung_e and rung_e.get("rankerUsed") != ["ltr"]:
        lines += [
            "",
            "> **Bậc E KHÔNG (luôn) chạy LambdaMART.** Bộ xếp hạng thực sự đã chạy "
            "qua {} lần: `{}`. Nếu có lần rơi về `linear`, thiếu `app/ltr/model.txt` "
            "khiến bậc E và D thành cùng một thuật toán ở lần đó — chênh lệch chỉ "
            "là nhiễu đo, không phải đóng góp của LTR.".format(
                repeats, ", ".join(rung_e.get("rankerUsed") or [])
            ),
        ]

    lines += [
        "",
        "## Bảng 2 — Đóng góp của từng tín hiệu",
        "",
        "Δ là mức nDCG thay đổi khi TẮT tín hiệu đó, tính THEO CẶP trong cùng một "
        "lần lặp (so với bậc D của chính lần đó) rồi mới lấy mean±std qua "
        "{} lần — kiểm soát được phần trôi do trending/popularity sống thay đổi "
        "giữa các lần lặp. Δ càng âm, tín hiệu càng quan trọng. Δ có std lớn gần "
        "bằng |mean| nghĩa là chưa đủ tin cậy để kết luận tín hiệu đó có tác động "
        "thật hay chỉ là nhiễu.".format(repeats),
        "",
        "| Tín hiệu | Trọng số | nDCG khi tắt (mean±std) | Δ (mean±std) |",
        "|---|---:|---|---|",
    ]
    for row in table2:
        lines.append(
            "| `{}` | {:.2f} | {:.4f} ± {:.4f} | {:+.4f} ± {:.4f} |".format(
                row["signal"], row["weight"], row["ndcgWithoutMean"], row["ndcgWithoutStd"],
                row["deltaMean"], row["deltaStd"],
            )
        )
    (RESULTS_DIR / "ablation_{}.md".format(stamp)).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    _svg_bars(
        "nDCG@{} theo cấu hình truy xuất (mean, {} lần lặp)".format(EVAL_K, repeats),
        [row["config"] for row in table1],
        [row["ndcgMean"] for row in table1],
        RESULTS_DIR / "ablation_{}_configs.svg".format(stamp),
    )
    _svg_bars(
        "Mức |Δ nDCG@{}| khi tắt từng tín hiệu (mean, {} lần lặp)".format(EVAL_K, repeats),
        [row["signal"] for row in table2],
        [abs(row["deltaMean"]) for row in table2],
        RESULTS_DIR / "ablation_{}_signals.svg".format(stamp),
    )
    print(
        "\nđã ghi results/ablation_{}.json|.md và hai biểu đồ SVG ({} lần lặp)".format(
            stamp, repeats
        )
    )


if __name__ == "__main__":
    main()
