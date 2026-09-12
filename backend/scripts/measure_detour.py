"""Đo hệ số vòng vèo thật: đường đi thực chia đường chim bay.

``spatio_temporal.eta_minutes`` ước lượng thời gian bằng khoảng cách THẲNG chia
vận tốc cố định, nên luôn lạc quan. Lộ trình đề xuất nhân một "hệ số kinh
nghiệm 1,35". Giờ đã có OSRM tự dựng thì không cần đoán nữa — đo trên chính các
POI của đồ án và lấy con số thật.

Chạy trong mạng của compose (cần cả Postgres lẫn OSRM):

    docker compose -p nearby-dev run --rm --no-deps \\
      -v "$PWD/backend/app:/app/app" -v "$PWD/backend/scripts:/app/scripts" \\
      -v "$PWD/backend/results:/app/results" \\
      backend python scripts/measure_detour.py --samples 200
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app import directions  # noqa: E402
from app.config import settings  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "results"

# Bốn tâm truy vấn thật, cùng bộ dùng cho bench_geo_channel để hai bài đo so
# sánh được với nhau.
ORIGINS = [
    ("Bến Thành", 10.7721, 106.6980),
    ("Thảo Điền", 10.8030, 106.7330),
    ("Phú Nhuận", 10.7990, 106.6800),
    ("Thủ Đức", 10.8700, 106.7800),
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def main() -> int:
    parser = argparse.ArgumentParser(description="Đo hệ số vòng vèo thật")
    parser.add_argument("--samples", type=int, default=200, help="Số POI mỗi tâm")
    args = parser.parse_args()

    if not settings.osrm_url:
        print("Chưa cấu hình OSRM_URL.")
        return 1

    rows: list[dict] = []
    for name, lat, lon in ORIGINS:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text AS id, name,
                           ST_Y(location::geometry) AS lat,
                           ST_X(location::geometry) AS lon
                    FROM pois
                    WHERE ST_DWithin(location,
                          ST_SetSRID(ST_Point(%(lon)s, %(lat)s), 4326)::geography, 5000)
                    ORDER BY id
                    LIMIT %(limit)s
                    """,
                    {"lat": lat, "lon": lon, "limit": args.samples},
                )
                pois = cursor.fetchall()

        for poi in pois:
            straight = haversine_m(lat, lon, poi["lat"], poi["lon"])
            # Bỏ cặp quá gần: chia cho một số rất nhỏ thổi phồng tỉ lệ lên vô
            # nghĩa (đi 5 m đường thẳng mà phải vòng 60 m thì tỉ lệ 12, nhưng
            # nó không nói gì về đường phố).
            if straight < 150:
                continue
            route = directions.route(lat, lon, poi["lat"], poi["lon"])
            if not route:
                continue
            rows.append(
                {
                    "origin": name,
                    "poi": poi["name"],
                    "straightM": round(straight, 1),
                    "roadM": route["distanceMeters"],
                    "ratio": round(route["distanceMeters"] / straight, 4),
                    "durationS": route["durationSeconds"],
                }
            )
        print(f"{name}: đã đo {len([r for r in rows if r['origin'] == name])} cặp")

    if not rows:
        print("Không đo được cặp nào.")
        return 1

    ratios = sorted(r["ratio"] for r in rows)
    def pct(f: float) -> float:
        return ratios[min(len(ratios) - 1, max(0, round(f * (len(ratios) - 1))))]

    summary = {
        "pairs": len(rows),
        "mean": round(statistics.fmean(ratios), 4),
        "median": round(statistics.median(ratios), 4),
        "p10": round(pct(0.10), 4),
        "p90": round(pct(0.90), 4),
        "min": round(ratios[0], 4),
        "max": round(ratios[-1], 4),
    }

    print()
    print("=== HỆ SỐ VÒNG VÈO (đường thật / đường chim bay) ===")
    for key, value in summary.items():
        print(f"  {key:>8}: {value}")
    print(f"\n  Lộ trình đề xuất hệ số kinh nghiệm 1.35 — đo thật được {summary['median']}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"detour_factor_{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nĐã ghi detour_factor_{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
