"""Lấy đánh giá thật từ Google Places cho POI nhập từ OpenStreetMap.

Chạy:
    # Thử 20 POI, KHÔNG ghi vào database — xem tỉ lệ khớp trước đã
    python scripts/enrich_ratings_google.py --limit 20 --dry-run

    # Chạy thật
    python scripts/enrich_ratings_google.py --limit 500

    # Làm mới bản ghi cũ hơn 30 ngày
    python scripts/enrich_ratings_google.py --limit 500 --max-age-days 30

Cần `GOOGLE_MAPS_API_KEY` trong môi trường (hoặc trong `.env`).

**Luôn chạy `--dry-run` trước.** Mỗi truy vấn đều tính tiền, và nếu bộ lọc
khớp đang quá chặt hoặc quá lỏng với dữ liệu của bạn thì tốt hơn hết là biết
điều đó sau 20 truy vấn chứ không phải sau 3000.

Sinh ra `results/google_ratings_<ts>.json` gồm TOÀN BỘ kết quả, kể cả các POI
bị từ chối kèm lý do — phần bị từ chối mới là phần cần đọc.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.poi_ratings import enrich, pending_pois, summarize  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description="Làm giàu rating từ Google Places")
    parser.add_argument("--limit", type=int, default=20, help="Số POI tối đa mỗi lượt")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gọi API nhưng KHÔNG ghi database (vẫn tính phí)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Dò lại POI có dữ liệu Google cũ hơn số ngày này",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.12,
        help="Giãn cách giữa hai truy vấn (giây)",
    )
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_MAPS_API_KEY") or settings.google_maps_api_key
    if not api_key:
        print(
            "Thiếu GOOGLE_MAPS_API_KEY.\n\n"
            "Cần key Google Cloud CỦA BẠN, đã bật 'Places API (New)' và gắn tài\n"
            "khoản thanh toán. Đặt vào file .env ở thư mục gốc:\n\n"
            "    GOOGLE_MAPS_API_KEY=<key cua ban>\n\n"
            "Nên giới hạn key theo API và theo IP trong Google Cloud Console."
        )
        return 1

    pois = pending_pois(args.limit, args.max_age_days)
    if not pois:
        print("Không còn POI nào cần dò. Có thể mọi POI đã có rating_source.")
        return 0

    mode = "THU (khong ghi database)" if args.dry_run else "GHI THAT"
    print(f"Dò {len(pois)} POI · chế độ {mode}\n")

    results = enrich(pois, api_key, sleep_seconds=args.sleep, dry_run=args.dry_run)

    for item in results:
        mark = "OK " if item.accepted else "BO "
        detail = item.matched_name or "-"
        extra = ""
        if item.accepted:
            extra = f" rating={item.rating} ({item.review_count} luot)"
        elif item.distance_meters is not None:
            extra = f" cach {item.distance_meters:.0f}m, giong {item.name_similarity:.2f}"
        print(f"{mark} {detail[:45]:45} {item.reason}{extra}")

    report = summarize(results)
    print(
        f"\nKhớp {report['accepted']}/{report['total']} "
        f"({report['acceptRate']:.0%}) · điểm trung bình {report['avgRating']}"
    )
    if report["rejectReasons"]:
        print("Lý do bỏ qua:")
        for reason, count in report["rejectReasons"].items():
            print(f"  {count:4}  {reason}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"google_ratings_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "measuredAt": stamp,
                "dryRun": args.dry_run,
                "summary": report,
                "results": [asdict(item) for item in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nChi tiết -> {out_path}")

    if args.dry_run:
        print("Đây là lượt THỬ — chưa ghi gì vào database. Bỏ --dry-run để ghi thật.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
