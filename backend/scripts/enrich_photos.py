"""Nạp trước ảnh Wikimedia Commons cho POI — điền sẵn cache `poi_photos`.

Chạy:
    # Thử 3 POI, KHÔNG ghi database — xem thật sự lấy được gì
    python scripts/enrich_photos.py --limit 3 --dry-run

    # Chạy thật
    python scripts/enrich_photos.py --limit 50

    # Chỉ POI có sẵn thẻ ảnh trong OSM, tức chỉ lấy ảnh 'place'
    python scripts/enrich_photos.py --limit 50 --only-tagged

Cần migration `0012_poi_photos_and_contact` đã áp dụng. Chưa có bảng
`poi_photo_fetches` thì không biết POI nào đã dò rồi, nên script dừng ngay và
in lệnh cần chạy thay vì dò lại từ đầu mỗi lượt.

**Luôn chạy `--dry-run` trước.** Không phải vì tiền — Wikimedia miễn phí — mà
vì thời gian: mỗi POI tốn 2-3 lần gọi, mỗi lần cách nhau tối thiểu một giây,
nên 50 POI đã là vài phút còn 3.010 POI là vài giờ. Nếu bộ lọc đang chọn sai
nhóm POI thì tốt hơn hết là biết sau 3 POI chứ không phải sau 3.000.

## Con số nào đi vào báo cáo

Tóm tắt cuối đếm RIÊNG ba nhóm, và chúng không được phép trộn vào nhau:

- POI có ảnh ``place`` — ảnh của CHÍNH địa điểm, suy từ thẻ OSM. Đếm thật: cả
  database chỉ có 7 POI mang thẻ ảnh, và 2 trong đó trỏ ra ngoài Wikimedia nên
  bị từ chối — còn 5 POI, tức 0,17%.
- POI chỉ có ảnh ``area`` — ảnh chụp quanh đó do geosearch tìm ra. Phần lớn là
  ảnh con phố, ảnh ô tô, ảnh logo.
- POI rỗng — đã hỏi Wikimedia và quanh đó thật sự không có ảnh nào.

Nhóm thứ tư, ``unavailable``, KHÔNG phải một kết quả: nó nghĩa là chưa gọi
được Wikimedia. Nó không được ghi vào cache và phải chạy lại, nên đừng cộng nó
vào nhóm rỗng khi viết báo cáo.

Sinh ra `results/poi_photos_<ts>.json` gồm TOÀN BỘ kết quả, kể cả POI không có
ảnh kèm lý do — phần thất bại mới là phần cần đọc, vì chỉ ở đó mới phân biệt
được "đã hỏi, không có ảnh" với "chưa hỏi được".

## Ba cái bẫy

**`--sleep` không thừa.** `app/photos.py` đã tự giãn một giây giữa hai request.
`--sleep` là khoảng nghỉ THÊM giữa hai POI: giãn nhịp trong tiến trình chỉ
bảo vệ được đúng ngưỡng tối thiểu, còn một lượt chạy dài hàng nghìn POI vẫn
đủ sức làm Wikimedia trả HTTP 429 (đã ăn thật). Hạ xuống dưới 1.0 là tự chuốc.

**`--only-tagged` ghi `empty` cho POI có thẻ hỏng.** Chế độ này bỏ hẳn bước
geosearch, nên một POI có thẻ ảnh nhưng thẻ trỏ tới file không còn tồn tại sẽ
được cache là `empty` (= "đã dò, không có ảnh") suốt `photo_cache_days` ngày,
dù quanh đó vẫn có thể có ảnh khu vực. Chạy `--only-tagged` là chấp nhận đánh
đổi đó; tóm tắt cuối sẽ liệt kê đúng những POI rơi vào trường hợp này.

**Hạ `--radius` xuống dưới bán kính của endpoint cũng ghi `empty` sai.** Mặc
định đã bám thẳng `photos.AREA_RADIUS_METERS`, nhưng truyền tay một con số nhỏ
hơn thì một POI chỉ có ảnh ở 200 m sẽ ra rỗng, và rỗng đó được cache là `empty`
(= "đã dò, không có ảnh") suốt `photo_cache_days` ngày. Bảng
`poi_photo_fetches` KHÔNG lưu bán kính, nên không ai biết kết luận đó chỉ đúng
với một bán kính hẹp — và endpoint sẽ không bao giờ dò lại ở bán kính đầy đủ.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Nhập nguyên module chứ không nhập từng hàm: `--radius` chỉ có hiệu lực khi
# đổi được hằng số ở tầng module (xem chú thích trong `main`).
from app import photos  # noqa: E402
from app.config import settings  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "results"

# Ba thẻ OSM duy nhất có thể dẫn tới ảnh CỦA CHÍNH địa điểm. Trong 3.000 bản
# ghi OSM đã nhập: image=2, wikimedia_commons=4, wikidata=5.
PHOTO_TAGS = ["image", "wikimedia_commons", "wikidata"]

# POI đã có dòng trong `poi_photo_fetches` là POI đã dò rồi — kể cả khi kết quả
# là rỗng. Đó chính là lý do bảng đó tồn tại.
_UNFETCHED_ONLY = "AND NOT EXISTS (SELECT 1 FROM poi_photo_fetches f WHERE f.poi_id = p.id)"

# Thẻ OSM lấy theo ĐÚNG quy tắc của `app.photos.poi_photo_context`: bản ghi
# nguồn mới nhất CÓ thẻ. Chọn khác đi thì script nạp ảnh theo một bộ thẻ còn
# endpoint đọc theo bộ thẻ khác, và sai lệch đó không ai nhìn thấy.
_PENDING_SQL = """
    WITH candidate AS (
        SELECT
            p.id::text AS id,
            p.name,
            ST_Y(p.location::geometry) AS latitude,
            ST_X(p.location::geometry) AS longitude,
            (
                SELECT r.raw_payload->'tags'
                FROM poi_source_records r
                WHERE r.canonical_poi_id = p.id AND r.raw_payload ? 'tags'
                ORDER BY r.last_seen_at DESC, r.id DESC
                LIMIT 1
            ) AS tags
        FROM pois p
        WHERE p.location IS NOT NULL
        {unfetched_only}
    )
    SELECT
        id, name, latitude, longitude, tags,
        COALESCE(tags ?| %(photo_tags)s::text[], FALSE) AS has_photo_tag
    FROM candidate
    WHERE NOT %(only_tagged)s OR COALESCE(tags ?| %(photo_tags)s::text[], FALSE)
    ORDER BY has_photo_tag DESC, id
    LIMIT %(limit)s
"""


@dataclass
class PhotoResult:
    """Kết quả dò một POI. ``outcome`` là thứ đi vào tóm tắt."""

    poi_id: str
    name: str | None
    has_photo_tag: bool
    # 'ready' | 'empty' | 'unavailable' — nguyên văn trạng thái của hợp đồng ảnh
    status: str
    # 'place' | 'area' | 'empty' | 'unavailable' — mịn hơn `status` một bậc, vì
    # 'ready' gộp cả ảnh thật lẫn ảnh khu vực mà báo cáo phải tách hai.
    outcome: str
    reason: str
    photo_count: int = 0
    place_count: int = 0
    area_count: int = 0
    nearest_area_meters: float | None = None
    photos: list[dict[str, Any]] = field(default_factory=list)


def photo_tables_ready(database_url: str | None = None) -> bool:
    """Migration 0012 đã chạy chưa."""
    with psycopg.connect(database_url or settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT to_regclass('public.poi_photos') IS NOT NULL
                   AND to_regclass('public.poi_photo_fetches') IS NOT NULL
                """
            )
            row = cursor.fetchone()
    return bool(row and row[0])


def pending_pois(
    limit: int,
    only_tagged: bool,
    skip_fetched: bool = True,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """POI cần nạp ảnh, POI có thẻ ảnh xếp trước.

    Xếp POI có thẻ lên đầu vì đó là nhóm duy nhất cho ra ảnh ``place``, và một
    lượt chạy bị cắt ngang giữa chừng thì nên đã kịp lấy xong phần quý nhất.

    ``skip_fetched=False`` chỉ dùng khi chưa có bảng cache và đang ``--dry-run``
    — lúc đó mọi POI đều coi như chưa dò.
    """
    sql = _PENDING_SQL.format(unfetched_only=_UNFETCHED_ONLY if skip_fetched else "")
    with psycopg.connect(database_url or settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                {"limit": limit, "only_tagged": only_tagged, "photo_tags": PHOTO_TAGS},
            )
            return [dict(row) for row in cursor.fetchall()]


def probe_only(poi: dict[str, Any], radius_m: int, only_tagged: bool) -> dict[str, Any]:
    """Dò Wikimedia cho một POI nhưng KHÔNG ghi gì — đường đi của ``--dry-run``.

    CỐ Ý lặp lại từng bước của ``app.photos.fetch_and_store``, bỏ đúng phần ghi
    database: thứ tự ứng viên, khử trùng tên file giữ lần xuất hiện đầu, và
    trần ``MAX_PHOTOS``. Sửa bên đó mà quên bên này thì ``--dry-run`` hứa một
    đằng còn lượt chạy thật ra một nẻo — kiểm lại cả hai mỗi khi
    ``fetch_and_store`` đổi.
    """
    probe: dict[str, Any] = {}

    candidates = photos.photos_from_tags(poi["tags"], probe)
    if not only_tagged and len(candidates) < photos.MAX_PHOTOS:
        candidates = candidates + photos.photos_near(
            float(poi["latitude"]), float(poi["longitude"]), radius_m, probe
        )

    ordered: list[photos.Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.title in seen:
            continue
        seen.add(candidate.title)
        ordered.append(candidate)

    details = photos.file_details([item.title for item in ordered], probe) if ordered else {}

    picked: list[dict[str, Any]] = []
    for candidate in ordered:
        if candidate.title not in details:
            continue
        picked.append(
            {
                "title": candidate.title,
                "confidence": candidate.confidence,
                "distanceMeters": candidate.dist if candidate.confidence == "area" else None,
            }
        )
        if len(picked) >= photos.MAX_PHOTOS:
            break

    # Phải kiểm cả `failed`, không được chỉ kiểm `not ok` — chép nguyên điều
    # kiện của `fetch_and_store` (app/photos.py:614). `probe["ok"]` chỉ BẬT LÊN
    # chứ không bao giờ tắt, nên geosearch trả 200 rồi imageinfo mới rớt vì hết
    # lượt thử 429 vẫn để lại ok=True với danh sách rỗng. Chỉ nhìn `ok` thì
    # --dry-run kết luận "đã dò, không có ảnh" cho một lần rớt mạng — mà con số
    # đó đi thẳng vào results/*.json rồi vào báo cáo, đúng thứ docstring đầu
    # file cấm ("đừng cộng unavailable vào nhóm rỗng").
    if not picked and (not probe.get("ok") or probe.get("failed")):
        return {"status": "unavailable", "photos": []}
    return {"status": "ready" if picked else "empty", "photos": picked}


def classify(
    poi: dict[str, Any], payload: dict[str, Any], radius_m: int, only_tagged: bool
) -> PhotoResult:
    """Quy một payload ảnh về một dòng kết quả đọc được."""
    found = payload.get("photos") or []
    place = [item for item in found if item.get("confidence") == "place"]
    area = [item for item in found if item.get("confidence") == "area"]
    distances = [
        item["distanceMeters"] for item in area if item.get("distanceMeters") is not None
    ]
    nearest = min(distances) if distances else None

    status = payload.get("status") or "unavailable"
    if place:
        outcome = "place"
        reason = f"{len(place)} anh cua chinh dia diem"
        if area:
            reason += f" + {len(area)} anh khu vuc"
    elif area:
        outcome = "area"
        reason = f"chi co anh khu vuc ({len(area)} anh"
        reason += f", gan nhat {nearest:.0f} m)" if nearest is not None else ")"
    elif status == "empty":
        outcome = "empty"
        reason = (
            "da do the OSM, khong co file anh dung duoc"
            if only_tagged
            else f"da do, Wikimedia khong co anh nao trong {radius_m} m"
        )
    else:
        outcome = "unavailable"
        # Không phải kết luận: không có dòng nào được ghi cache, chạy lại là dò lại.
        reason = "khong goi duoc Wikimedia — CHUA ket luan duoc, phai chay lai"

    return PhotoResult(
        poi_id=str(poi["id"]),
        name=poi.get("name"),
        has_photo_tag=bool(poi.get("has_photo_tag")),
        status=status,
        outcome=outcome,
        reason=reason,
        photo_count=len(found),
        place_count=len(place),
        area_count=len(area),
        nearest_area_meters=nearest,
        photos=found,
    )


def summarize(results: list[PhotoResult]) -> dict[str, Any]:
    """Đếm thô, không tỉ lệ làm tròn: các con số này đi thẳng vào báo cáo."""
    by_outcome = {"place": 0, "area": 0, "empty": 0, "unavailable": 0}
    for item in results:
        by_outcome[item.outcome] = by_outcome.get(item.outcome, 0) + 1
    return {
        "total": len(results),
        "withPlacePhotos": by_outcome["place"],
        "areaOnly": by_outcome["area"],
        "empty": by_outcome["empty"],
        "unavailable": by_outcome["unavailable"],
        "taggedSelected": sum(1 for item in results if item.has_photo_tag),
        "photoTotal": sum(item.photo_count for item in results),
        "placePhotoTotal": sum(item.place_count for item in results),
        "areaPhotoTotal": sum(item.area_count for item in results),
    }


_MARKS = {"place": "ANH", "area": "KHU", "empty": "---", "unavailable": "?  "}


def main() -> int:
    parser = argparse.ArgumentParser(description="Nạp trước ảnh Wikimedia cho POI")
    parser.add_argument("--limit", type=int, default=50, help="Số POI tối đa mỗi lượt")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gọi Wikimedia nhưng KHÔNG ghi database",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.2,
        help="Nghỉ thêm giữa hai POI (giây). Wikimedia chặn thật khi gọi dồn.",
    )
    # Mặc định phải bám ĐÚNG hằng số của endpoint chứ không phải một con số
    # riêng của script: `poi_photo_fetches` không có cột bán kính, nên 'empty'
    # ghi xuống là lời khẳng định vô điều kiện "quanh đây không có ảnh" và
    # `cached_photos` trả lại nó suốt `photo_cache_days` ngày. Dò hẹp hơn
    # endpoint = ghi một kết luận sai mà endpoint không bao giờ dò lại để sửa.
    # Đọc ở đây là đọc TRƯỚC dòng gán `photos.AREA_RADIUS_METERS` bên dưới, nên
    # vẫn lấy được giá trị gốc.
    parser.add_argument(
        "--radius",
        type=int,
        default=photos.AREA_RADIUS_METERS,
        help=(
            "Bán kính tìm ảnh khu vực (m). Mặc định bám hằng số của endpoint "
            "(%(default)s m). Hạ xuống là tự ghi 'empty' sai vào cache."
        ),
    )
    parser.add_argument(
        "--only-tagged",
        action="store_true",
        help="Chỉ POI có thẻ image/wikimedia_commons/wikidata, tức chỉ lấy ảnh 'place'",
    )
    args = parser.parse_args()

    if not settings.photos_enabled:
        print(
            "PHOTOS_ENABLED đang tắt. Script này chỉ nạp cache cho một tính năng\n"
            "đang tắt — bật lại rồi chạy, hoặc bỏ qua nếu đang cố ý đo độ trễ sạch."
        )
        return 1

    tables_ready = photo_tables_ready()
    if not tables_ready and not args.dry_run:
        print(
            "Chưa có bảng poi_photos/poi_photo_fetches — migration\n"
            "0012_poi_photos_and_contact chưa được áp dụng. Chạy trước:\n\n"
            "    docker compose run --rm migrate\n\n"
            "Không có bảng cache thì không biết POI nào đã dò rồi, và mỗi lượt\n"
            "chạy sẽ hỏi lại Wikimedia từ đầu."
        )
        return 1

    # `fetch_and_store` đọc bán kính từ hằng số ở tầng module chứ không nhận
    # tham số, nên đây là cách duy nhất để `--radius` có hiệu lực mà không sửa
    # app/photos.py. Gán trước khi gọi bất cứ hàm nào của module đó.
    photos.AREA_RADIUS_METERS = args.radius

    pois = pending_pois(args.limit, args.only_tagged, skip_fetched=tables_ready)
    if not pois:
        print("Không còn POI nào cần nạp ảnh. Có thể mọi POI đã có bản ghi dò.")
        return 0

    mode = "THU (khong ghi database)" if args.dry_run else "GHI THAT"
    scope = "chi POI co the anh" if args.only_tagged else f"ban kinh {args.radius} m"
    print(f"Nạp ảnh cho {len(pois)} POI · {scope} · chế độ {mode}")
    if not tables_ready:
        print(
            "CẢNH BÁO: chưa có bảng cache, đang coi MỌI POI là chưa dò. Lượt chạy\n"
            "thật sẽ chọn khác — chạy `docker compose run --rm migrate` trước."
        )
    print()

    results: list[PhotoResult] = []
    for poi in pois:
        try:
            if args.dry_run:
                payload = probe_only(poi, args.radius, args.only_tagged)
            else:
                payload = photos.fetch_and_store(
                    poi["id"],
                    # `--only-tagged` được cài bằng cách giấu toạ độ: không có
                    # lat/lng thì `fetch_and_store` bỏ hẳn bước geosearch. Đó
                    # đúng là định nghĩa của chế độ này, không phải mẹo vặt.
                    None if args.only_tagged else poi["latitude"],
                    None if args.only_tagged else poi["longitude"],
                    poi["tags"],
                )
        except KeyboardInterrupt:
            # Một lượt 3.000 POI chạy hàng giờ. Ctrl-C mà mất luôn file kết quả
            # của phần đã chạy thì lần sau phải dò lại từ đầu để biết đã thấy gì.
            print("\nDừng theo yêu cầu — vẫn ghi lại phần đã chạy.\n")
            break
        except Exception as error:  # noqa: BLE001 - một POI lỗi không làm hỏng cả lượt
            payload = {"status": "unavailable", "photos": []}
            results.append(classify(poi, payload, args.radius, args.only_tagged))
            results[-1].reason = f"loi: {error}"
            print(f"{_MARKS['unavailable']} {str(poi.get('name') or '-')[:42]:42} loi: {error}")
            time.sleep(args.sleep)
            continue

        item = classify(poi, payload, args.radius, args.only_tagged)
        results.append(item)
        print(f"{_MARKS[item.outcome]} {str(item.name or '-')[:42]:42} {item.reason}")
        time.sleep(args.sleep)

    report = summarize(results)
    print(f"\nTóm tắt {report['total']} POI:")
    print(f"  {report['withPlacePhotos']:4}  có ảnh CỦA CHÍNH địa điểm ('place')")
    print(f"  {report['areaOnly']:4}  chỉ có ảnh khu vực ('area')")
    print(f"  {report['empty']:4}  đã dò, không có ảnh nào ('empty')")
    print(f"  {report['unavailable']:4}  chưa dò được, phải chạy lại ('unavailable')")
    print(
        f"  {report['photoTotal']:4}  ảnh tổng cộng "
        f"({report['placePhotoTotal']} 'place' + {report['areaPhotoTotal']} 'area')"
    )
    print(f"  {report['taggedSelected']:4}  POI được chọn có sẵn thẻ ảnh trong OSM")

    if args.only_tagged:
        narrowed = [item for item in results if item.outcome == "empty"]
        if narrowed:
            print(
                f"\n{len(narrowed)} POI có thẻ ảnh nhưng thẻ không dùng được. Ở chế độ\n"
                f"--only-tagged chúng bị ghi 'empty' (= đã dò, không ảnh) trong "
                f"{settings.photo_cache_days} ngày,\ndù quanh đó vẫn có thể có ảnh khu vực:"
            )
            for item in narrowed:
                print(f"  {item.poi_id}  {item.name}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"poi_photos_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "measuredAt": stamp,
                "dryRun": args.dry_run,
                # Bán kính và --only-tagged đổi hẳn ý nghĩa của các con số trên,
                # nên đọc lại file sau này mà không có chúng là đọc mò.
                "options": {
                    "limit": args.limit,
                    "sleep": args.sleep,
                    "radius": args.radius,
                    "onlyTagged": args.only_tagged,
                },
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
