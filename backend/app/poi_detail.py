"""Trang chi tiết một địa điểm — lắp ráp từ Postgres, KHÔNG gọi mạng ra ngoài.

Endpoint này phải nhanh vì nó nằm trên đường tới của mỗi lần người dùng bấm vào
một kết quả. Mọi thứ chậm — cụ thể là ảnh Wikimedia — nằm ở endpoint riêng
(`app/photos.py`) để giao diện gọi song song và trang hiện ra trước, ảnh điền
vào sau.

Ba chỗ nói thật mà file này phải giữ:

**Giờ mở.** ``weekHours`` luôn có ĐÚNG 7 phần tử, thứ Hai trước. Khi cột
``opening_hours`` chưa parse được thì cả 7 ngày mang ``unknown=true`` và giao
diện hiện nguyên văn chuỗi OSM. Đoán ra một lịch mở cửa từ một chuỗi không đọc
được là loại sai số tệ nhất: nó trông hợp lý.

**Đánh giá.** ``reviewSummary`` đếm từ bảng ``poi_reviews``, mà bảng đó hiện
đang RỖNG. ``count=0, average=null`` là câu trả lời đúng. Chú ý nó KHÁC
``reviewCount`` lấy từ cột ``pois.review_count`` (dữ liệu seed/bên thứ ba) —
hai con số khác nguồn nên để hai trường khác nhau thay vì gộp thành một con số
không ai truy được gốc.

**Điểm.** ``rating`` là ``null`` với 2.982/3.010 POI. NULL không phải 0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row

from .config import settings
from .opening_hours import opening_status
from .spatio_temporal import DEFAULT_TIMEZONE, eta_minutes, windowed_popularity

DATABASE_URL = settings.database_url

# Thứ tự theo `datetime.weekday()` (0 = thứ Hai), trùng với `DAY_INDEX` của
# `opening_hours`. Dùng chung một quy ước ở cả ba chỗ, nếu không thì lịch mở cửa
# lệch đúng một ngày — sai số im lặng nhất trong cả file này.
WEEKDAY_LABELS = (
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
)

# Trạng thái parse do file này đặt thêm, cho hai trường hợp mà
# `parse_opening_hours` không gắn nhãn:
#   'missing'    — POI không có thẻ opening_hours nào.
#   'always_open' được quy về 'parsed' chứ không thành nhãn riêng, vì "24/7"
#                 ĐÃ được đọc hiểu thành công; giao diện chỉ cần biết có nên
#                 tin `weekHours` hay không.
PARSE_STATUS_MISSING = "missing"

SIMILAR_RADIUS_METERS = 2_000
SIMILAR_LIMIT = 6
REVIEW_LIMIT = 10

_POI_QUERY = """
    SELECT
        id::text AS id, name, description, category,
        category_label AS "categoryLabel", address,
        district, city, country_code AS "countryCode",
        ST_Y(location::geometry) AS latitude,
        ST_X(location::geometry) AS longitude,
        brand, website, phone,
        rating::float8 AS rating, review_count AS "reviewCount",
        rating_source AS "ratingSource",
        popularity_score AS "popularityScore", price_level AS "priceLevel",
        tags, amenities,
        (sponsored_until IS NOT NULL AND sponsored_until > NOW()) AS sponsored,
        timezone, opening_hours AS "openingHours",
        source, source_id AS "sourceId", updated_at AS "updatedAt",
        h3_r7, h3_r8, h3_r9, embedding_model AS "embeddingModel",
        CASE
            WHEN CAST(%(latitude)s AS double precision) IS NULL THEN NULL
            ELSE ST_Distance(
                location,
                ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography
            )
        END AS "distanceMeters"
    FROM pois
    WHERE id = %(poi_id)s
"""

_SIMILAR_QUERY = """
    SELECT
        id::text AS id, name, category_label AS "categoryLabel", address,
        rating::float8 AS rating, review_count AS "reviewCount",
        ST_Y(location::geometry) AS latitude,
        ST_X(location::geometry) AS longitude,
        ST_Distance(
            location,
            ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography
        ) AS "distanceMeters"
    FROM pois
    WHERE category = %(category)s
      AND id <> %(poi_id)s
      AND ST_DWithin(
          location,
          ST_SetSRID(ST_Point(%(longitude)s, %(latitude)s), 4326)::geography,
          %(radius)s
      )
    ORDER BY "distanceMeters" ASC
    LIMIT %(limit)s
"""

_REVIEWS_QUERY = """
    SELECT id::text AS id, author_name AS "authorName", rating, title, body,
           language, source, helpful_count AS "helpfulCount", created_at
    FROM poi_reviews
    WHERE poi_id = %(poi_id)s
    ORDER BY created_at DESC
    LIMIT %(limit)s
"""

_REVIEW_HISTOGRAM_QUERY = """
    SELECT rating::int AS stars, COUNT(*)::int AS total
    FROM poi_reviews
    WHERE poi_id = %(poi_id)s
    GROUP BY rating
"""


def distance_label(meters: float | None) -> str | None:
    """"cách 320 m" / "cách 1,4 km" — dấu phẩy thập phân theo cách viết tiếng Việt."""
    if meters is None:
        return None
    if meters < 1_000:
        # Làm tròn tới chục mét: khoảng cách đường chim bay chính xác tới từng
        # mét là độ chính xác giả, vì người dùng đi theo đường phố chứ không
        # theo đường thẳng.
        return f"cách {int(round(meters / 10.0)) * 10} m"
    return f"cách {meters / 1000.0:.1f} km".replace(".", ",")


def normalize_opening_hours(schedule: dict[str, Any] | None) -> dict[str, Any]:
    """Bổ đủ bốn khoá của hợp đồng cho cột ``opening_hours``.

    Cột này lưu đúng thứ ``parse_opening_hours`` trả về, mà hàm đó bỏ qua
    ``parseStatus`` ở nhánh "24/7" và trả hẳn ``{}`` khi POI không có giờ. Giao
    diện thì cần bốn khoá luôn có mặt để khỏi phải đoán.

    "24/7" được ghi là ``parsed``: chuỗi đó ĐÃ đọc hiểu được, và ``weekHours``
    dựng từ nó là đúng — khác hẳn với ``unsupported``, nơi ta thật sự không
    biết gì.
    """
    schedule = schedule if isinstance(schedule, dict) else {}
    always_open = bool(schedule.get("alwaysOpen"))
    raw = schedule.get("raw")
    if always_open:
        parse_status = "parsed"
    elif schedule.get("parseStatus"):
        parse_status = schedule["parseStatus"]
    else:
        parse_status = PARSE_STATUS_MISSING
    return {
        "raw": raw,
        "parseStatus": parse_status,
        "periods": schedule.get("periods") or [],
        "alwaysOpen": always_open,
    }


def week_hours(
    schedule: dict[str, Any], timezone_name: str, at: datetime | None = None
) -> list[dict[str, Any]]:
    """Bảy ngày trong tuần, thứ Hai trước. Luôn đủ 7 phần tử.

    ``unknown`` và ``closed`` là hai chuyện khác nhau và không bao giờ được gộp:
    ``unknown=true`` là "không đọc được giờ mở của địa điểm này",
    ``closed=true`` là "ngày này địa điểm đóng cửa". Gộp lại thì 73 POI ghi giờ
    theo kiểu OSM lạ sẽ hiện ra như những quán đóng cửa cả tuần.
    """
    try:
        zone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    today = (at or datetime.now(zone)).astimezone(zone).weekday()

    unknown = schedule["parseStatus"] != "parsed"
    always_open = schedule["alwaysOpen"]

    days: list[dict[str, Any]] = []
    for weekday in range(7):
        if unknown:
            intervals: list[dict[str, str]] = []
        elif always_open:
            # "24:00" chứ không phải "00:00": hiển thị "00:00 – 00:00" trông
            # như đóng cửa.
            intervals = [{"opens": "00:00", "closes": "24:00"}]
        else:
            # Ca qua nửa đêm chỉ được OSM gắn vào NGÀY BẮT ĐẦU, nhưng phần sau
            # 00:00 thuộc về ngày kế. `opening_status` đã trải period ra hôm
            # qua/hôm nay/ngày mai (`_concrete_intervals`), nên nếu ở đây không
            # trải theo thì hai trường trong CÙNG một phản hồi nói ngược nhau:
            # badge "Đang mở · còn 180 phút" nằm ngay trên dòng "Hôm nay đóng
            # cửa". Ca đã cắn thật: 'Mo-Sa 08:00-06:00' lúc 03:00 Chủ Nhật.
            #
            # So sánh NGẶT và loại riêng closes == "00:00": parser quy "24:00"
            # thành "00:00", nên 'Mo-Su 11:00-24:00' vẫn kết thúc đúng lúc nửa
            # đêm — phần tràn dài 0 phút, thêm vào sẽ dựng ra khoảng
            # "00:00 – 00:00" và biến một ngày đóng cửa thành ngày mở.
            #
            # So chuỗi "HH:MM" hợp lệ vì parser đã đệm 0 cho giờ một chữ số,
            # thứ tự chuỗi trùng thứ tự thời gian.
            previous = (weekday - 1) % 7
            intervals = []
            for period in schedule["periods"]:
                # `period_days` chứ không phải `days`: `days` là danh sách bảy
                # ngày đang gom ở ngoài vòng lặp này, đặt trùng tên thì mỗi
                # period ghi đè mất nó và `append` bên dưới còn bơm thẳng dict
                # ngày vào `schedule["periods"][i]["days"]`.
                period_days = period.get("days", [])
                if weekday in period_days:
                    intervals.append({"opens": period["opens"], "closes": period["closes"]})
                if (
                    previous in period_days
                    and period["closes"] < period["opens"]
                    and period["closes"] != "00:00"
                ):
                    intervals.append({"opens": "00:00", "closes": period["closes"]})
            intervals.sort(key=lambda interval: interval["opens"])
        days.append(
            {
                "weekday": weekday,
                "label": WEEKDAY_LABELS[weekday],
                "isToday": weekday == today,
                "closed": not unknown and not intervals,
                "unknown": unknown,
                "intervals": intervals,
            }
        )
    return days


def _review_block(cursor: Any, poi_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cursor.execute(_REVIEW_HISTOGRAM_QUERY, {"poi_id": poi_id})
    histogram = {str(star): 0 for star in range(1, 6)}
    total = 0
    weighted = 0
    for row in cursor.fetchall():
        stars = row["stars"]
        # Cộng dồn NẰM TRONG guard: điểm nào không vào được biểu đồ thì cũng
        # không được vào trung bình, nếu không thì con số trung bình và cái
        # biểu đồ ngay cạnh nó nói hai chuyện khác nhau. Hiện `poi_reviews` có
        # CHECK (rating BETWEEN 1 AND 5) nên nhánh này không chạy, nhưng ràng
        # buộc đó nằm ở database chứ không ở đây.
        if 1 <= stars <= 5:
            histogram[str(stars)] = row["total"]
            total += row["total"]
            weighted += stars * row["total"]
    # Trung bình tính từ chính histogram nên không lệch so với biểu đồ người
    # dùng nhìn thấy. `None` khi chưa có đánh giá nào — KHÔNG phải 0.0.
    average = round(weighted / total, 2) if total else None

    cursor.execute(_REVIEWS_QUERY, {"poi_id": poi_id, "limit": REVIEW_LIMIT})
    reviews = [
        {
            "id": row["id"],
            "authorName": row["authorName"],
            "rating": row["rating"],
            "title": row["title"],
            "body": row["body"],
            "language": row["language"],
            "source": row["source"],
            "helpfulCount": row["helpfulCount"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in cursor.fetchall()
    ]
    return {"count": total, "average": average, "histogram": histogram}, reviews


def fetch_detail(
    poi_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
    at: datetime | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    """Toàn bộ dữ liệu trang chi tiết. ``None`` khi không có POI đó.

    ``latitude``/``longitude`` là vị trí NGƯỜI DÙNG (tuỳ chọn). Thiếu nó thì
    ``distanceMeters`` và ``etaMinutes`` là ``null`` — không có vị trí xuất phát
    thì không có khoảng cách, và điền một con số vào đó là bịa.
    """
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                _POI_QUERY,
                {"poi_id": poi_id, "latitude": latitude, "longitude": longitude},
            )
            row = cursor.fetchone()
            if row is None:
                return None

            similar_rows = []
            if row["category"]:
                cursor.execute(
                    _SIMILAR_QUERY,
                    {
                        "poi_id": poi_id,
                        "category": row["category"],
                        "latitude": row["latitude"],
                        "longitude": row["longitude"],
                        "radius": SIMILAR_RADIUS_METERS,
                        "limit": SIMILAR_LIMIT,
                    },
                )
                similar_rows = cursor.fetchall()

            review_summary, reviews = _review_block(cursor, poi_id)

    timezone_name = row["timezone"] or DEFAULT_TIMEZONE
    schedule = normalize_opening_hours(row["openingHours"])

    distance = row["distanceMeters"]
    # Dùng `eta_minutes` trần, KHÔNG nhân hệ số giao thông như `enrich_candidates`
    # làm cho danh sách kết quả: hệ số đó cần một truy vấn mật độ ping cho cả
    # thành phố, quá đắt cho một trang chi tiết. Hệ quả phải biết: ETA ở đây có
    # thể thấp hơn ETA cùng POI trong danh sách vài phút. Con số chính xác nằm ở
    # `/api/v1/directions` (OSRM, đường đi có thật).
    eta = eta_minutes(distance) if distance is not None else None

    try:
        # `.get` trả None khi POI này chưa có tương tác nào trong 24h — đó là
        # "đã đếm, được 0", một sự thật. Quy về 0 NGAY TẠI ĐÂY để nhánh `except`
        # bên dưới còn giữ được `None` cho một ý nghĩa khác hẳn.
        windows = windowed_popularity([poi_id], database_url).get(poi_id) or {
            "w15": 0,
            "w1h": 0,
            "w24h": 0,
        }
    except psycopg.Error:
        # Không đếm được (DB rớt, `ingestion_events` bị khoá/mất quyền). Để
        # `null` chứ không trả ba số 0: hiện có 27 POI thật sự có tương tác
        # trong 24h, một lần rớt kết nối mà in ra "24h 0" là nói SAI về chúng
        # chứ không chỉ là nói thiếu. Cùng lý do với ba trạng thái
        # ready/empty/unavailable của endpoint ảnh.
        #
        # `windowed_popularity` mở một kết nối RIÊNG sau khi khối `with` ở trên
        # đã đóng, nên nhánh này không phải lý thuyết: nó là một lần chạm DB độc
        # lập, rớt được một mình trong khi cả trang còn lại đã lấy xong.
        windows = None

    similar = []
    for candidate in similar_rows:
        label = distance_label(candidate["distanceMeters"])
        similar.append(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "categoryLabel": candidate["categoryLabel"],
                "address": candidate["address"],
                "rating": candidate["rating"],
                "reviewCount": candidate["reviewCount"],
                "distanceMeters": candidate["distanceMeters"],
                "latitude": candidate["latitude"],
                "longitude": candidate["longitude"],
                "reason": f"Cùng loại · {label}" if label else "Cùng loại",
            }
        )

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "categoryLabel": row["categoryLabel"],
        "address": row["address"],
        "district": row["district"],
        "city": row["city"],
        "countryCode": row["countryCode"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "brand": row["brand"],
        "website": row["website"],
        "phone": row["phone"],
        "rating": row["rating"],
        "reviewCount": row["reviewCount"],
        "ratingSource": row["ratingSource"],
        "popularityScore": row["popularityScore"],
        "priceLevel": row["priceLevel"],
        "tags": list(row["tags"] or []),
        "amenities": row["amenities"] or {},
        "sponsored": bool(row["sponsored"]),
        "timezone": timezone_name,
        "openingHours": schedule,
        # Gọi lại `opening_status` trên dữ liệu GỐC chứ không dùng cột
        # `pois.open_now`: cột đó là ảnh chụp lúc nhập dữ liệu, đúng vào đúng một
        # thời điểm trong quá khứ.
        "openingStatus": opening_status(row["openingHours"], timezone_name, at),
        "weekHours": week_hours(schedule, timezone_name, at),
        "distanceMeters": distance,
        "etaMinutes": eta,
        # `null` = CHƯA ĐẾM ĐƯỢC, khác hẳn ba số 0 = "đã đếm, chưa có ai".
        "popularityWindows": windows,
        "reviewSummary": review_summary,
        "reviews": reviews,
        "similar": similar,
        "provenance": {
            "source": row["source"],
            "sourceId": row["sourceId"] or None,
            "updatedAt": row["updatedAt"].isoformat(),
            "h3": {"r7": row["h3_r7"], "r8": row["h3_r8"], "r9": row["h3_r9"]},
            "embeddingModel": row["embeddingModel"],
        },
    }
