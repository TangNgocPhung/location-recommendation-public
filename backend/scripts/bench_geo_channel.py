"""Đo độ trễ kênh không gian: geo_distance so với tra vành hexagon H3.

Đây là phần "đo và so sánh" của bước B3 trong lộ trình. Câu hỏi cần trả lời
bằng số, không phải bằng lập luận: **lọc bằng một phép tra chỉ mục đảo có rẻ
hơn lọc bằng phép tính khoảng cách trên từng document không, và rẻ hơn bao
nhiêu trên đúng tập dữ liệu của đồ án này.**

Cách đo, và lý do từng chi tiết:

- **Chạy trong cùng một tiến trình, cùng một client, xen kẽ hai kênh.** Đo
  xong hết kênh A rồi mới sang kênh B thì chênh lệch có thể chỉ là cache của
  OpenSearch ấm dần lên chứ không phải khác biệt giữa hai cách lọc.
- **Có vòng làm nóng và vòng đó bị vứt đi.** Truy vấn nguội trên máy phát triển
  từng đo được 3.329 ms so với 603 ms lúc ấm — gấp năm lần, đủ để nuốt trọn mọi
  khác biệt thật.
- **Báo p50 và p95, không chỉ trung bình.** Trung bình giấu đuôi, mà đuôi mới
  là thứ người dùng cảm nhận.
- **Đối chiếu tập kết quả.** Nhanh hơn mà trả về ít POI hơn thì không phải
  nhanh hơn, chỉ là làm ít việc hơn. Cột `recall` cho biết vành hexagon giữ lại
  bao nhiêu phần kết quả của geo_distance, và `extra` cho biết nó trả dư bao
  nhiêu (phần dư là dự kiến — vành phủ trùm hình tròn, PostGIS cắt lại sau).

Chạy:
    docker compose -p nearby-dev run --rm --no-deps \\
      -v "$PWD/backend/app:/app/app" -v "$PWD/backend/scripts:/app/scripts" \\
      -v "$PWD/backend/results:/app/results" \\
      backend python scripts/bench_geo_channel.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.poi_features import h3_ring_ids  # noqa: E402
from app.search import query as query_builder  # noqa: E402
from app.search.client import get_client  # noqa: E402
from app.search.index import INDEX_NAME  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "results"

# Bốn tâm truy vấn có mật độ POI khác hẳn nhau, để con số không chỉ đúng ở một
# chỗ. Toạ độ lấy từ các mốc thật của TP.HCM.
CENTERS = [
    ("Bến Thành", 10.7721, 106.6980),
    ("Thảo Điền", 10.8030, 106.7330),
    ("Phú Nhuận", 10.7990, 106.6800),
    ("Thủ Đức", 10.8700, 106.7800),
]
RADII = (500, 1_000, 3_000, 5_000)
PER_CHANNEL_SIZE = 150


def timed_ids(client, body: dict) -> tuple[float, set[str]]:
    started = time.perf_counter()
    response = client.search(index=INDEX_NAME, body=body)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, set(query_builder.extract_ranked_ids(response))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Đo độ trễ geo_distance vs H3 ring")
    parser.add_argument("--repeats", type=int, default=30, help="Số lần đo mỗi cấu hình")
    parser.add_argument("--warmup", type=int, default=5, help="Số vòng làm nóng bị vứt đi")
    args = parser.parse_args()

    client = get_client()
    if client is None:
        print("Không kết nối được OpenSearch. Cần chạy trong mạng của compose.")
        return 1

    total_docs = client.count(index=INDEX_NAME).get("count", 0)
    print(f"Chỉ mục {INDEX_NAME}: {total_docs} document\n")

    rows: list[dict] = []
    for name, latitude, longitude in CENTERS:
        for radius in RADII:
            ring = h3_ring_ids(latitude, longitude, radius)
            if ring is None:
                print(f"{name} {radius} m: vành vượt trần số ô, bỏ qua")
                continue

            geo_body = query_builder.geo_body(
                latitude, longitude, radius, None, PER_CHANNEL_SIZE
            )
            h3_body = query_builder.h3_body(
                ring.cells, ring.field, latitude, longitude, None, PER_CHANNEL_SIZE
            )

            for _ in range(args.warmup):
                timed_ids(client, geo_body)
                timed_ids(client, h3_body)

            geo_times: list[float] = []
            geo_control_times: list[float] = []
            h3_times: list[float] = []
            geo_ids: set[str] = set()
            h3_ids: set[str] = set()
            # Xen kẽ: bất kỳ thứ gì trôi theo thời gian (cache, tải máy, GC) đều
            # tác động lên hai kênh như nhau thay vì dồn hết vào kênh đo sau.
            #
            # Đo geo_distance HAI LẦN mỗi vòng là phép ĐỐI CHỨNG, và đây là chi
            # tiết quan trọng nhất của cả bài đo: chênh lệch giữa hai lần đo
            # cùng một truy vấn chính là mức nhiễu nền. Không có nó thì mọi
            # chênh lệch nhỏ giữa hai kênh đều bị đọc thành "kênh này nhanh
            # hơn", trong khi nó có thể chỉ là máy đang bận.
            for _ in range(args.repeats):
                elapsed, ids = timed_ids(client, geo_body)
                geo_times.append(elapsed)
                geo_ids = ids
                elapsed, ids = timed_ids(client, h3_body)
                h3_times.append(elapsed)
                h3_ids = ids
                elapsed, _ = timed_ids(client, geo_body)
                geo_control_times.append(elapsed)

            shared = len(geo_ids & h3_ids)
            row = {
                "center": name,
                "radiusM": radius,
                "h3Resolution": ring.resolution,
                "h3RingK": ring.k,
                "h3CellCount": len(ring.cells),
                "geoMeanMs": round(statistics.fmean(geo_times), 2),
                "geoP50Ms": round(percentile(geo_times, 0.50), 2),
                "geoP95Ms": round(percentile(geo_times, 0.95), 2),
                "h3MeanMs": round(statistics.fmean(h3_times), 2),
                "h3P50Ms": round(percentile(h3_times, 0.50), 2),
                "h3P95Ms": round(percentile(h3_times, 0.95), 2),
                "geoControlMeanMs": round(statistics.fmean(geo_control_times), 2),
                "geoHits": len(geo_ids),
                "h3Hits": len(h3_ids),
                "recall": round(shared / len(geo_ids), 4) if geo_ids else None,
                "extra": len(h3_ids - geo_ids),
            }
            row["speedup"] = (
                round(row["geoMeanMs"] / row["h3MeanMs"], 2) if row["h3MeanMs"] else None
            )
            # Nhiễu nền = chênh lệch giữa hai lần đo CÙNG một truy vấn.
            row["noiseMs"] = round(abs(row["geoMeanMs"] - row["geoControlMeanMs"]), 2)
            row["diffMs"] = round(row["h3MeanMs"] - row["geoMeanMs"], 2)
            # Chênh lệch chỉ đáng kể khi nó lớn hơn nhiễu nền.
            row["significant"] = abs(row["diffMs"]) > row["noiseMs"]
            rows.append(row)
            print(
                f"{name:<10} {radius:>5} m  r{ring.resolution} k={ring.k:<2} "
                f"{len(ring.cells):>3} o | geo {row['geoMeanMs']:>7.2f} | "
                f"h3 {row['h3MeanMs']:>7.2f} | chenh {row['diffMs']:>+7.2f} | "
                f"nhieu {row['noiseMs']:>6.2f} | "
                f"{'DANG KE' if row['significant'] else 'trong nhieu'} | "
                f"recall {row['recall']} | du {row['extra']}"
            )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": stamp,
        "indexDocuments": total_docs,
        "repeats": args.repeats,
        "warmup": args.warmup,
        "perChannelSize": PER_CHANNEL_SIZE,
        "rows": rows,
    }
    json_path = RESULTS_DIR / f"bench_geo_channel_{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Độ trễ kênh không gian — geo_distance so với vành hexagon H3",
        "",
        f"- Chỉ mục: **{total_docs} document**",
        f"- Mỗi cấu hình: {args.warmup} vòng làm nóng (vứt) + {args.repeats} lần đo, hai kênh xen kẽ",
        f"- `size` mỗi kênh: {PER_CHANNEL_SIZE}",
        "",
        "| Tâm | Bán kính | Ô H3 | geo TB | H3 TB | Chênh | Nhiễu nền | Kết luận | Recall | Dư |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['center']} | {row['radiusM']} m | "
            f"r{row['h3Resolution']}-k{row['h3RingK']}-{row['h3CellCount']} | "
            f"{row['geoMeanMs']} ms | {row['h3MeanMs']} ms | "
            f"{row['diffMs']:+} ms | {row['noiseMs']} ms | "
            f"{'đáng kể' if row['significant'] else 'trong nhiễu'} | "
            f"{row['recall']} | {row['extra']} |"
        )
    if rows:
        geo_all = statistics.fmean(r["geoMeanMs"] for r in rows)
        h3_all = statistics.fmean(r["h3MeanMs"] for r in rows)
        noise_all = statistics.fmean(r["noiseMs"] for r in rows)
        significant = [r for r in rows if r["significant"]]
        faster = [r for r in significant if r["diffMs"] < 0]
        slower = [r for r in significant if r["diffMs"] > 0]
        recalls = [r["recall"] for r in rows if r["recall"] is not None]
        lines += [
            "",
            "## Kết luận đọc thẳng từ bảng trên",
            "",
            f"- Trung bình: geo_distance **{geo_all:.2f} ms**, H3 **{h3_all:.2f} ms**, "
            f"nhiễu nền trung bình **{noise_all:.2f} ms**.",
            f"- Số cấu hình có chênh lệch vượt nhiễu nền: **{len(significant)}/{len(rows)}** "
            f"({len(faster)} nghiêng về H3, {len(slower)} nghiêng về geo_distance).",
            f"- Recall của vành hexagon so với geo_distance: "
            f"**{min(recalls):.2f} - {max(recalls):.2f}**.",
            "",
            "> Cột `Nhiễu nền` là chênh lệch giữa HAI lần đo cùng một truy vấn",
            "> geo_distance trong cùng một vòng. Chênh lệch giữa hai kênh chỉ được",
            "> coi là đáng kể khi nó lớn hơn con số này - nếu không, thứ đang được",
            "> đo là tải máy chứ không phải cách lọc.",
            "",
            "> Cột `Recall` là phần kết quả của geo_distance mà vành hexagon giữ được;",
            "> cột `Dư` là số POI vành trả thêm vì nó phủ trùm hình tròn. Phần dư này",
            "> bị `hydrate_candidates` cắt lại bằng `ST_DWithin`, nên nó tốn băng thông",
            "> chứ không làm sai kết quả.",
        ]
    md_path = RESULTS_DIR / f"bench_geo_channel_{stamp}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nĐã ghi {json_path.name} và {md_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
