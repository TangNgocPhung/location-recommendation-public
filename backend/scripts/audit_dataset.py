"""Phase 6 — kiểm tra định kỳ chất lượng tập dữ liệu LTR, KHÔNG huấn luyện.

Chạy (cần Postgres đang chạy):

    python scripts/audit_dataset.py
    python scripts/audit_dataset.py --source click   # chỉ audit nguồn click
    python scripts/audit_dataset.py --json           # in JSON thô, không bảng

Mục đích: trả lời "dataset đã đủ tốt để sang Phase 7 chưa?" bằng một BỘ tiêu
chí, không phải một con số cứng kiểu "1000 dòng là đủ". Nhiều request_id, mỗi
request nhiều POI, có cả positive lẫn negative, feature không thiếu quá nhiều
— thiếu một trong số đó thì số dòng lớn cỡ nào cũng không giúp LambdaMART học
được thứ gì có ý nghĩa.

Không ghi ``model.txt``, không train gì — chỉ đọc dữ liệu hiện có và báo cáo.
So sánh với ``scripts/train_ltr.py``: script đó huấn luyện thật (và tự audit
dữ liệu như một bước phụ); script này CHỈ audit, dùng để chạy lặp lại theo
thời gian xem dataset đã lớn/sạch tới đâu.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.ltr.dataset import (  # noqa: E402
    Dataset,
    build_from_clicks,
    build_from_judgments,
    merge,
)
from app.ltr.train import MIN_GROUPS_FOR_CLAIM  # noqa: E402

JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"

# Đặc trưng CỐT LÕI phải phủ cao thì mô hình mới học được thứ gì đó — khác với
# rating/review_count, vốn ĐƯỢC PHÉP phủ thấp vì đa số POI chưa có review thật
# (xem app/ltr/features.py: rating chỉ tính khi rating_source='user').
CORE_FEATURES = (
    "distance_meters",
    "spatial_decay",
    "text_score",
    "bm25_score",
    "fusion_score_norm",
)
CORE_FEATURE_MIN_COVERAGE = 0.8
MIN_POSITIVE_RATE = 0.02
MAX_POSITIVE_RATE = 0.98
# Nhóm 1 POI không có gì để lambdarank so sánh cặp — "group count" cao mà phần
# lớn chỉ có 1 POI thì con số đó không có ý nghĩa huấn luyện. Đòi ĐA SỐ nhóm có
# >=2 POI thay vì chỉ nhìn trung bình, vì trung bình bị vài nhóm lớn che mất
# việc phần lớn nhóm nhỏ.
MIN_GROUP_GE2_RATIO = 0.8


def _criteria(summary: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """(tên tiêu chí, đạt hay không, giải thích số liệu). Không tiêu chí nào
    một mình quyết định — in đủ cả bộ để người đọc tự thấy dataset yếu ở đâu."""
    n_groups = summary["usableGroups"]
    label_keys = summary["labelDistribution"]
    positive_rate = summary["positiveRate"]
    coverage = summary["featureCoverage"]
    weak_core = [name for name in CORE_FEATURES if coverage.get(name, 0.0) < CORE_FEATURE_MIN_COVERAGE]
    total_groups = summary["groups"]
    ge2_ratio = summary["groupsWithGe2Pois"] / total_groups if total_groups else 0.0

    return [
        (
            f"Đủ nhóm dùng được (>= {MIN_GROUPS_FOR_CLAIM})",
            n_groups >= MIN_GROUPS_FOR_CLAIM,
            f"{n_groups} nhóm có ít nhất 1 candidate liên quan",
        ),
        (
            f"Phần lớn nhóm có >=2 POI (>= {MIN_GROUP_GE2_RATIO:.0%})",
            ge2_ratio >= MIN_GROUP_GE2_RATIO,
            f"{summary['groupsWithGe2Pois']}/{total_groups} nhóm có >=2 POI "
            f"({ge2_ratio:.0%}), {summary['groupsWith1Poi']} nhóm chỉ có 1 POI "
            "(không có gì để so sánh cặp)",
        ),
        (
            "Label có biến thiên (cả positive lẫn negative)",
            len(label_keys) >= 2 and MIN_POSITIVE_RATE <= positive_rate <= MAX_POSITIVE_RATE,
            f"phân bố nhãn {label_keys}, positiveRate={positive_rate:.2%}",
        ),
        (
            "Feature cốt lõi không thiếu quá nhiều",
            not weak_core,
            "đủ phủ" if not weak_core else f"phủ thấp: {', '.join(weak_core)}",
        ),
    ]


def _print_table(summary: dict[str, Any]) -> None:
    print("\n=== Dataset — tổng quan ===")
    rows_by_key = [
        ("Số nhóm (request_id)", summary["groups"]),
        ("  trong đó dùng được cho LambdaMART", summary["usableGroups"]),
        ("Số dòng (request_id, poi_id)", summary["rows"]),
        ("  trong đó dùng được", summary["usableRows"]),
        ("Nhóm bị bỏ (skipped)", summary["skipped"]),
        ("Positive rate", f"{summary['positiveRate']:.2%}"),
        ("Phân bố nhãn", summary["labelDistribution"]),
        (
            "Kích thước nhóm (min/mean/max)",
            f"{summary['groupSize']['min']} / {summary['groupSize']['mean']} / {summary['groupSize']['max']}",
        ),
        (
            "  trong đó nhóm có >=2 POI (hữu ích cho ranking)",
            f"{summary['groupsWithGe2Pois']}/{summary['groups']}",
        ),
        ("  nhóm chỉ có 1 POI (không so sánh cặp được)", summary["groupsWith1Poi"]),
        ("Theo nguồn nhãn", summary["bySource"]),
    ]
    for label, value in rows_by_key:
        print(f"  {label:42s} {value}")

    print("\n=== Snapshot coverage (chỉ nguồn click) ===")
    src = summary["clickFeatureSource"]
    total_click_groups = src["snapshot"] + src["recomputed"]
    row_coverage = summary["clickSnapshotRowCoverage"]
    print(f"  Nhóm dùng snapshot / tổng nhóm click       {src['snapshot']} / {total_click_groups}")
    print(
        "  Tỉ lệ DÒNG lấy từ snapshot (mục tiêu ~100%) "
        + (f"{row_coverage:.2%}" if row_coverage is not None else "— (chưa có nhóm click nào)")
    )
    if row_coverage is not None and row_coverage < 1.0:
        print(
            "  → phần recomputed là traffic TRƯỚC migration 0013 (ranking_snapshots)."
            " Sẽ tự giảm dần khi traffic mới tích lũy, không cần làm gì thêm."
        )

    print("\n=== Độ phủ đặc trưng ===")
    for name, value in sorted(summary["featureCoverage"].items(), key=lambda item: item[1]):
        flag = " <-- thấp" if name in CORE_FEATURES and value < CORE_FEATURE_MIN_COVERAGE else ""
        print(f"  {name:22s} {value:.0%}{flag}")

    print("\n=== Sẵn sàng cho Phase 7 (LightGBM)? — không một con số nào tự quyết ===")
    criteria = _criteria(summary)
    for name, passed, detail in criteria:
        mark = "✅" if passed else "❌"
        print(f"  {mark} {name}")
        print(f"      {detail}")
    if all(passed for _, passed, _ in criteria):
        print("\n  => Đủ điều kiện thử train (scripts/train_ltr.py --source both).")
    else:
        print(
            "\n  => CHƯA đủ. Không nên train ngay dù có nhiều dòng — thiếu bất kỳ"
            " tiêu chí nào ở trên cũng khiến mô hình học sai hoặc học rỗng."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit dataset LTR (Phase 6) — không train")
    parser.add_argument("--source", choices=("judgment", "click", "both"), default="both")
    parser.add_argument("--radius", type=int, default=5_000, help="Bán kính cho nhóm click")
    parser.add_argument("--json", action="store_true", help="Chỉ in JSON, không in bảng")
    parser.add_argument("--no-save", action="store_true", help="Không ghi báo cáo vào results/")
    args = parser.parse_args()

    parts: list[Dataset] = []
    if args.source in ("judgment", "both") and JUDGMENTS_PATH.exists():
        parts.append(build_from_judgments(JUDGMENTS_PATH))
    if args.source in ("click", "both"):
        parts.append(build_from_clicks(radius=args.radius))
    dataset = merge(parts)
    summary = dataset.summary()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_table(summary)

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = RESULTS_DIR / f"dataset_audit_{stamp}.json"
        out_path.write_text(
            json.dumps({"summary": summary, "skippedDetail": dataset.skipped[:20]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not args.json:
            print(f"\nBáo cáo -> {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
