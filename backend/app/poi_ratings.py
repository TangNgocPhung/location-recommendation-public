"""Lấy đánh giá thật từ Google Places API (New) cho POI nhập từ OpenStreetMap.

OpenStreetMap không có trường đánh giá, nên 2982/3010 POI trong database không
có rating. Module này dò từng POI sang Google Places để lấy `rating` và
`userRatingCount` thật.

## Vì sao phải có bộ lọc khớp, không lấy kết quả đầu tiên

`places:searchText` luôn trả về *một cái gì đó*. Tìm "Tạp Hóa Hoàng" ở Quận 11
hoàn toàn có thể ra một tiệm tạp hoá cùng tên ở quận khác, hoặc một cửa hàng
tên na ná cách đó 3 km. Nhận bừa kết quả đầu nghĩa là gán điểm của cửa hàng
NÀY cho cửa hàng KHÁC — sai âm thầm, không một dòng lỗi nào, và sai lệch đó
sẽ chảy thẳng vào bảng kết quả của báo cáo.

Nên mỗi kết quả phải qua hai cửa:

1. **Khoảng cách** — tâm Google phải nằm trong ``MAX_MATCH_METERS`` so với toạ
   độ OSM. OSM và Google đặt điểm khác nhau đôi chút (cổng vào vs. tâm nhà),
   nên ngưỡng không thể quá chặt, nhưng 150 m đủ loại nhầm sang phố khác.
2. **Tên** — độ tương đồng token sau khi bỏ dấu và bỏ tiền tố loại hình
   ("quán", "nhà hàng", "tiệm"...) phải đạt ``MIN_NAME_SIMILARITY``.

POI nào không qua được thì để NGUYÊN rating NULL và ghi lý do. Thiếu dữ liệu
trung thực tốt hơn dữ liệu sai.

## Điều khoản và chi phí — đọc trước khi chạy

- Cần **API key của chính bạn** kèm tài khoản thanh toán Google Cloud. Mỗi
  truy vấn đều tính phí; 3010 POI là 3010 truy vấn.
- Điều khoản của Google giới hạn việc lưu trữ lâu dài nội dung Places. Place
  ID là trường được phép lưu lâu dài; điểm số và số lượt đánh giá thì cần làm
  mới định kỳ. Cột ``rating_fetched_at`` tồn tại để biết bản ghi đã cũ bao
  lâu, và ``--max-age-days`` để làm mới.
- Trong báo cáo phải ghi rõ rating là dữ liệu Google, không phải đánh giá của
  người dùng ứng dụng này. Cột ``rating_source`` giữ đúng thông tin đó.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .config import settings

logger = logging.getLogger("nearby-ratings")

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Trường xin về. Xin đúng thứ cần dùng, vì Google tính phí theo nhóm trường:
# càng xin nhiều nhóm càng đắt.
FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
    )
)

# Tâm Google và tâm OSM lệch nhau là bình thường (cổng vào vs. tâm toà nhà),
# nhưng quá 150 m thì gần như chắc chắn là địa điểm khác.
MAX_MATCH_METERS = 150.0
MIN_NAME_SIMILARITY = 0.55

# Từ chỉ loại hình, không mang thông tin định danh. "Quán Cà Phê Hoàng" và
# "Cà Phê Hoàng" là một chỗ; giữ lại các từ này làm điểm tương đồng bị thổi
# phồng giữa hai quán khác hẳn nhau nhưng cùng loại.
_GENERIC_TOKENS = {
    "quan", "quan an", "nha hang", "tiem", "cua hang", "shop", "store",
    "cafe", "ca phe", "coffee", "tap hoa", "sieu thi", "cho", "tiem tap hoa",
    "restaurant", "the", "chi nhanh", "cn", "co so",
}


@dataclass
class MatchResult:
    """Kết quả dò một POI. ``accepted=False`` luôn kèm ``reason``."""

    poi_id: str
    accepted: bool
    reason: str
    place_id: str | None = None
    rating: float | None = None
    review_count: int | None = None
    matched_name: str | None = None
    distance_meters: float | None = None
    name_similarity: float | None = None


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    # đ/Đ không phải dấu tổ hợp nên NFD không tách được, phải thay tay.
    return without_marks.replace("đ", "d").replace("Đ", "D")


def normalize_name(value: str | None) -> list[str]:
    """Đưa tên về danh sách token đã bỏ dấu, chữ thường, bỏ từ chỉ loại hình."""
    if not value:
        return []
    plain = strip_accents(value).lower()
    plain = re.sub(r"[^a-z0-9\s]", " ", plain)
    tokens = [token for token in plain.split() if token]
    # Bỏ cụm hai từ chỉ loại hình trước, rồi mới bỏ từ đơn.
    joined = " ".join(tokens)
    for phrase in sorted(_GENERIC_TOKENS, key=len, reverse=True):
        if " " in phrase:
            joined = joined.replace(phrase, " ")
    tokens = [t for t in joined.split() if t and t not in _GENERIC_TOKENS]
    return tokens


def name_similarity(left: str | None, right: str | None) -> float:
    """Jaccard trên tập token đã chuẩn hóa, trong [0,1].

    Dùng Jaccard chứ không dùng khoảng cách ký tự vì tên địa điểm tiếng Việt
    hay đảo trật tự và thêm bớt từ ("Cà Phê Hoàng" / "Hoàng Coffee"), mà thứ
    tự thì không nên bị phạt.
    """
    left_tokens, right_tokens = set(normalize_name(left)), set(normalize_name(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(intersection) / len(union)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def evaluate_match(poi: dict[str, Any], place: dict[str, Any]) -> MatchResult:
    """Quyết định một ứng viên Google có phải cùng địa điểm với POI hay không.

    Hàm thuần, không I/O — đây là chỗ dễ sai nhất nên phải kiểm thử được mà
    không cần API key.
    """
    poi_id = str(poi["id"])
    place_id = place.get("id")
    matched_name = (place.get("displayName") or {}).get("text")
    location = place.get("location") or {}
    latitude, longitude = location.get("latitude"), location.get("longitude")

    if latitude is None or longitude is None:
        return MatchResult(poi_id, False, "ket qua khong co toa do", place_id, matched_name=matched_name)

    distance = haversine_meters(poi["latitude"], poi["longitude"], latitude, longitude)
    similarity = name_similarity(poi.get("name"), matched_name)

    if distance > MAX_MATCH_METERS:
        return MatchResult(
            poi_id, False, f"cach {distance:.0f} m (> {MAX_MATCH_METERS:.0f} m)",
            place_id, matched_name=matched_name,
            distance_meters=distance, name_similarity=similarity,
        )
    if similarity < MIN_NAME_SIMILARITY:
        return MatchResult(
            poi_id, False, f"ten khac nhau (Jaccard {similarity:.2f})",
            place_id, matched_name=matched_name,
            distance_meters=distance, name_similarity=similarity,
        )

    rating = place.get("rating")
    if rating is None:
        return MatchResult(
            poi_id, False, "khop dung dia diem nhung Google cung chua co danh gia",
            place_id, matched_name=matched_name,
            distance_meters=distance, name_similarity=similarity,
        )

    return MatchResult(
        poi_id,
        True,
        "khop",
        place_id=place_id,
        rating=float(rating),
        review_count=int(place.get("userRatingCount") or 0),
        matched_name=matched_name,
        distance_meters=distance,
        name_similarity=similarity,
    )


def search_place(
    poi: dict[str, Any], api_key: str, radius_meters: float = 200.0
) -> list[dict[str, Any]]:
    """Gọi ``places:searchText`` quanh toạ độ POI. Trả danh sách ứng viên thô.

    Dùng ``urllib`` của thư viện chuẩn, cùng cách với ``poi_import`` gọi
    Overpass: ảnh phục vụ không cần thêm phụ thuộc HTTP chỉ vì một script làm
    giàu dữ liệu chạy ngoài luồng.
    """
    payload = {
        "textQuery": poi["name"],
        "languageCode": "vi",
        "regionCode": "VN",
        "maxResultCount": 5,
        # `locationBias` chứ không phải `locationRestriction`: hạn chế cứng sẽ
        # loại mất địa điểm đúng khi Google đặt tâm lệch vài chục mét ra ngoài
        # vòng tròn. Lọc khoảng cách đã làm ở `evaluate_match`, chặt chẽ hơn.
        "locationBias": {
            "circle": {
                "center": {"latitude": poi["latitude"], "longitude": poi["longitude"]},
                "radius": radius_meters,
            }
        },
    }
    request = urllib.request.Request(
        PLACES_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("places", []) or []


_PENDING_SQL = """
    SELECT id::text AS id, name,
           ST_Y(location::geometry) AS latitude,
           ST_X(location::geometry) AS longitude
    FROM pois
    WHERE source = 'openstreetmap'
      AND name IS NOT NULL AND name <> ''
      AND (
            rating_source IS NULL
         OR (rating_source = 'google'
             AND (rating_fetched_at IS NULL
                  OR rating_fetched_at < NOW() - make_interval(days => %(max_age_days)s)))
      )
    ORDER BY id
    LIMIT %(limit)s
"""


def pending_pois(
    limit: int, max_age_days: int, database_url: str | None = None
) -> list[dict[str, Any]]:
    """POI cần dò: chưa từng dò, hoặc dữ liệu Google đã quá hạn làm mới."""
    with psycopg.connect(database_url or settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(_PENDING_SQL, {"limit": limit, "max_age_days": max_age_days})
            return [dict(row) for row in cursor.fetchall()]


def save_match(result: MatchResult, database_url: str | None = None) -> None:
    """Ghi kết quả khớp. Chỉ ghi khi ``accepted`` — không bao giờ ghi đè rating
    của người dùng ứng dụng (``rating_source = 'user'``) bằng số của Google."""
    if not result.accepted:
        return
    with psycopg.connect(database_url or settings.database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pois SET
                    rating = %(rating)s,
                    review_count = %(review_count)s,
                    rating_source = 'google',
                    rating_fetched_at = NOW(),
                    google_place_id = %(place_id)s
                WHERE id = %(poi_id)s::uuid
                  AND (rating_source IS NULL OR rating_source <> 'user')
                """,
                {
                    "rating": result.rating,
                    "review_count": result.review_count,
                    "place_id": result.place_id,
                    "poi_id": result.poi_id,
                },
            )


def enrich(
    pois: Iterable[dict[str, Any]],
    api_key: str,
    sleep_seconds: float = 0.12,
    dry_run: bool = False,
    database_url: str | None = None,
) -> list[MatchResult]:
    """Dò và (nếu không dry-run) ghi rating cho từng POI. Trả toàn bộ kết quả,
    kể cả các POI không khớp — phần không khớp mới là thứ cần xem lại."""
    results: list[MatchResult] = []
    for poi in pois:
        try:
            candidates = search_place(poi, api_key)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:200]
            logger.warning("Dò %r lỗi HTTP %s: %s", poi.get("name"), error.code, detail)
            results.append(
                MatchResult(str(poi["id"]), False, f"HTTP {error.code}: {detail}")
            )
            # 401/403 là sai key hoặc chưa bật API — mọi POI sau sẽ hỏng y hệt,
            # dừng ngay thay vì đốt thêm truy vấn.
            if error.code in (401, 403):
                logger.error("Key bị từ chối — dừng lượt chạy.")
                break
            continue
        except Exception as error:  # noqa: BLE001 - một POI lỗi không làm hỏng cả lượt
            logger.warning("Dò %r lỗi: %s", poi.get("name"), error)
            results.append(MatchResult(str(poi["id"]), False, f"loi goi API: {error}"))
            continue

        if not candidates:
            results.append(MatchResult(str(poi["id"]), False, "Google khong tra ket qua nao"))
        else:
            # Xét mọi ứng viên, nhận cái ĐẦU TIÊN qua được cả hai cửa; kết quả
            # hạng nhất của Google không phải lúc nào cũng đúng chỗ.
            evaluated = [evaluate_match(poi, place) for place in candidates]
            accepted = next((item for item in evaluated if item.accepted), None)
            results.append(accepted or evaluated[0])

        if not dry_run:
            save_match(results[-1], database_url)
        # Giãn nhịp để không đụng giới hạn tần suất của Google.
        time.sleep(sleep_seconds)
    return results


def summarize(results: list[MatchResult]) -> dict[str, Any]:
    accepted = [item for item in results if item.accepted]
    reasons: dict[str, int] = {}
    for item in results:
        if item.accepted:
            continue
        key = re.sub(r"[\d.]+", "N", item.reason)
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "total": len(results),
        "accepted": len(accepted),
        "rejected": len(results) - len(accepted),
        "acceptRate": round(len(accepted) / len(results), 4) if results else 0.0,
        "avgRating": (
            round(sum(item.rating or 0 for item in accepted) / len(accepted), 2)
            if accepted
            else None
        ),
        "rejectReasons": dict(sorted(reasons.items(), key=lambda pair: -pair[1])),
    }
