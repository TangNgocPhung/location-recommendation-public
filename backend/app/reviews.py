"""Đánh giá 1-5 sao thật — explicit feedback (khác implicit: impression/click/
navigation).

    Người dùng
        ↓
    Xem POI / đến POI
        ↓
    Đánh giá 1-5 sao
        ↓
    poi_reviews
        ↓
    aggregate (AVG + COUNT)
        ↓
    pois.rating / pois.review_count (rating_source = 'user')

`poi_reviews` đã tồn tại từ migration 0003 và đã có đường ĐỌC (`poi_detail.
_review_block`) — module này là đường GHI còn thiếu. Aggregate tái dùng đúng
cặp cột `pois.rating`/`review_count` mà `poi_ratings.py` (nguồn Google) đã
dùng, theo đúng thứ tự ưu tiên đã đặt ra ở migration 0009: user > google >
seed. `poi_ratings.save_match` đã tự chặn ghi đè khi `rating_source = 'user'`,
nên aggregate ở đây không cần lo bị một lần chạy Google job sau đó xoá mất.

CHƯA đưa ratingMean/ratingCount vào `ranking_snapshots.FEATURE_KEYS` hay
`ltr/features.py` — quyết định có chủ đích: dữ liệu review thật hiện còn quá
ít để làm feature huấn luyện có ý nghĩa. Khi có đủ, thêm vào hai chỗ đó là đủ,
không cần sửa gì ở đây.

Cột 0 không có nghĩa "0 sao" — `review_count = 0` là NOT NULL DEFAULT 0 nên
luôn có giá trị, còn `rating = NULL` (không phải 0.0) khi chưa ai đánh giá,
đúng bất biến "chưa có dữ liệu" đã áp dụng nhất quán từ migration 0008.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings

DATABASE_URL = settings.database_url

_INSERT_SQL = """
    INSERT INTO poi_reviews (poi_id, user_id, rating, title, body, source)
    VALUES (%(poi_id)s::uuid, %(user_id)s, %(rating)s, %(title)s, %(body)s, 'user')
    RETURNING id::text AS id, created_at
"""

# AVG/COUNT tính lại từ TOÀN BỘ poi_reviews của POI đó mỗi lần — không cộng dồn
# tăng dần, để một review bị sửa/xoá sau này (chưa có endpoint, nhưng schema
# không cấm) không làm số liệu trôi khỏi sự thật hiện có trong bảng.
_AGGREGATE_SQL = """
    UPDATE pois SET
        rating = agg.average,
        review_count = agg.total,
        rating_source = 'user',
        rating_fetched_at = NOW()
    FROM (
        SELECT AVG(rating)::numeric(2, 1) AS average, COUNT(*)::int AS total
        FROM poi_reviews
        WHERE poi_id = %(poi_id)s::uuid
    ) AS agg
    WHERE pois.id = %(poi_id)s::uuid
    RETURNING pois.rating, pois.review_count
"""


def submit_review(
    poi_id: str,
    session_id: str,
    rating: int,
    title: str | None = None,
    body: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    """Ghi một đánh giá và cập nhật lại pois.rating/review_count ngay trong
    cùng transaction. Trả None nếu poi_id không tồn tại."""
    database_url = database_url or DATABASE_URL
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pois WHERE id = %(poi_id)s::uuid", {"poi_id": poi_id})
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                _INSERT_SQL,
                {
                    "poi_id": poi_id,
                    "user_id": session_id,
                    "rating": rating,
                    "title": title,
                    "body": body or "",
                },
            )
            review_row = cursor.fetchone()
            cursor.execute(_AGGREGATE_SQL, {"poi_id": poi_id})
            summary_row = cursor.fetchone()
        connection.commit()
    average = summary_row["rating"]
    return {
        "reviewId": review_row["id"],
        "createdAt": review_row["created_at"].isoformat(),
        "ratingMean": float(average) if average is not None else None,
        "ratingCount": summary_row["review_count"],
    }
