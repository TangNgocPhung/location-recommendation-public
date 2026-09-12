"""Phase 4 + 5 — dựng tập huấn luyện rồi huấn luyện LambdaMART.

Chạy (cần Postgres + OpenSearch đang chạy):

    python scripts/train_ltr.py --source judgment
    python scripts/train_ltr.py --source both --k 10 --folds 5

Sinh ra:
  - ``app/ltr/model.txt``          — mô hình để đường phục vụ nạp
  - ``app/ltr/model.meta.json``    — tham số, số liệu k-fold, danh sách đặc trưng
  - ``results/ltr_dataset_<ts>.jsonl``  — tập huấn luyện thô (kiểm chứng được)
  - ``results/ltr_train_<ts>.json``     — báo cáo đầy đủ
  - ``results/ltr_train_<ts>.md``       — bảng dán thẳng vào báo cáo

Script CỐ Ý không ghi ``model.txt`` khi tập dữ liệu quá nhỏ, trừ khi truyền
``--force``. Lý do: một mô hình huấn luyện trên vài nhóm sẽ tự tin đưa ra thứ
hạng trông rất thuyết phục mà không có cơ sở nào, và một khi file đã nằm đó thì
mọi phép đo sau đều mặc định chạy qua nó.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.ltr import train as ltr_train  # noqa: E402
from app.ltr.dataset import (  # noqa: E402
    build_from_clicks,
    build_from_judgments,
    merge,
)

JUDGMENTS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "relevance_judgments.json"
RESULTS_DIR = PROJECT_ROOT / "results"
MODEL_DIR = PROJECT_ROOT / "app" / "ltr"


def _markdown(report: dict, data_report: dict, stamp: str) -> str:
    lines = [
        f"# Huấn luyện LTR — {stamp}",
        "",
        "## Tập dữ liệu",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---:|",
        f"| Nhóm truy vấn | {data_report['groups']} |",
        f"| Nhóm dùng được (có ít nhất 1 ứng viên liên quan) | {data_report['usableGroups']} |",
        f"| Tổng cặp (truy vấn, POI) | {data_report['rows']} |",
        f"| Nhãn dương | {data_report['positives']} |",
        f"| Nhóm bị bỏ | {data_report['skipped']} |",
        "",
        "### Độ phủ đặc trưng",
        "",
        "Tỉ lệ dòng có giá trị (không thiếu). Đặc trưng phủ thấp thì mô hình",
        "gần như không học được gì từ nó — con số này phải nằm trong báo cáo.",
        "",
        "| Đặc trưng | Độ phủ |",
        "|---|---:|",
    ]
    for name, value in sorted(
        data_report["featureCoverage"].items(), key=lambda item: -item[1]
    ):
        lines.append(f"| `{name}` | {value:.0%} |")

    lines += [
        "",
        "## Kết quả huấn luyện",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---:|",
        f"| nDCG@{report['k']} (trung bình {report['folds']}-fold theo nhóm) | "
        f"{report['ndcgMean']} |",
        f"| Độ lệch chuẩn giữa các fold | {report['ndcgStd']} |",
        f"| Từng fold | {report['ndcgPerFold']} |",
        "",
    ]
    if report["insufficientData"]:
        lines += [
            "> **Cảnh báo — dữ liệu chưa đủ để kết luận.**",
            f"> Chỉ có {report['groups']} nhóm truy vấn dùng được, dưới ngưỡng "
            f"{report['minGroupsForClaim']}. Con số nDCG ở trên chứng minh đường ống",
            "> chạy thông, KHÔNG chứng minh LambdaMART tốt hơn trọng số tay.",
            "> Muốn kết luận thì phải gán nhãn thêm truy vấn hoặc thu thập thêm click.",
            "",
        ]

    lines += ["## Mức quan trọng của đặc trưng (gain)", "", "| Đặc trưng | Gain |", "|---|---:|"]
    for name, value in list(report["featureImportance"].items())[:15]:
        lines.append(f"| `{name}` | {value} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Huấn luyện mô hình LTR cho Nearby")
    parser.add_argument(
        "--source",
        choices=("judgment", "click", "both"),
        default="judgment",
        help="Nguồn nhãn. 'judgment' là nguồn chính; 'click' có position bias.",
    )
    parser.add_argument("--k", type=int, default=10, help="k của nDCG@k")
    parser.add_argument("--folds", type=int, default=5, help="Số fold theo nhóm")
    parser.add_argument("--radius", type=int, default=5_000, help="Bán kính cho nhóm click")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Vẫn ghi model.txt dù tập dữ liệu dưới ngưỡng tin cậy",
    )
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    parts = []
    if args.source in ("judgment", "both"):
        print(f"Dựng nhóm từ phán quyết liên quan: {JUDGMENTS_PATH}")
        parts.append(build_from_judgments(JUDGMENTS_PATH))
    if args.source in ("click", "both"):
        print("Dựng nhóm từ log click...")
        parts.append(build_from_clicks(radius=args.radius))
    dataset = merge(parts)

    data_report = ltr_train.dataset_report(dataset)
    print(json.dumps(data_report, ensure_ascii=False, indent=2)[:2000])

    jsonl_path = RESULTS_DIR / f"ltr_dataset_{stamp}.jsonl"
    written = dataset.to_jsonl(jsonl_path)
    print(f"Đã ghi {written} dòng -> {jsonl_path}")

    if not dataset.usable_groups:
        print(
            "\nDỪNG: không nhóm nào có ứng viên liên quan. Không thể huấn luyện.\n"
            "Cần gán nhãn thêm trong tests/fixtures/relevance_judgments.json "
            "(dùng judgment_template.json làm khung) hoặc thu thập thêm click."
        )
        return 1

    report = ltr_train.train(dataset, k=args.k, folds=args.folds)
    print(
        f"\nnDCG@{args.k} = {report['ndcgMean']} ± {report['ndcgStd']} "
        f"trên {report['groups']} nhóm / {report['rows']} dòng"
    )

    json_path = RESULTS_DIR / f"ltr_train_{stamp}.json"
    md_path = RESULTS_DIR / f"ltr_train_{stamp}.md"
    payload = {key: value for key, value in report.items() if key != "model"}
    payload["dataset"] = data_report
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(report, data_report, stamp), encoding="utf-8")
    print(f"Báo cáo -> {json_path}\n           {md_path}")

    if report["insufficientData"] and not args.force:
        print(
            f"\nKHÔNG ghi model.txt: chỉ {report['groups']} nhóm, dưới ngưỡng "
            f"{report['minGroupsForClaim']}.\n"
            "Đường ống đã chạy thông và báo cáo đã có. Thêm nhãn rồi chạy lại, "
            "hoặc dùng --force nếu chỉ muốn thử nghiệm tích hợp."
        )
        return 0

    paths = ltr_train.save(report, MODEL_DIR)
    print(f"Mô hình -> {paths['model']}\n           {paths['meta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
