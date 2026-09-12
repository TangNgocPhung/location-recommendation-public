"""Tính feature offline từ Postgres (nguồn sự thật để huấn luyện & materialize).

Đây là "offline store" của feature platform: các hàm đọc lịch sử từ
``ingestion_events`` + ``pois`` và trả về feature đã tính, chưa ghi đi đâu.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..config import settings
from .registry import region_key

logger = logging.getLogger(__name__)

DATABASE_URL = settings.database_url

# Trọng số loại sự kiện — thống nhất với ranking.fetch_category_affinity.
_EVENT_WEIGHTS = "CASE e.event_type WHEN 'poi_click' THEN 1 WHEN 'navigation_start' THEN 3 WHEN 'review' THEN 5 ELSE 0 END"

_USER_PROFILE_SQL = f"""
    SELECT e.session_id::text AS session_id, p.category AS category,
           SUM({_EVENT_WEIGHTS})::float8 AS weight,
           COUNT(*)::int AS events,
           AVG(NULLIF(p.price_level, 0))::float8 AS avg_price
    FROM ingestion_events e
    JOIN pois p ON p.id::text = e.poi_id
    WHERE e.event_type IN ('poi_click', 'navigation_start', 'review')
    GROUP BY e.session_id, p.category
"""

# Impression THẬT mang metadata.request_id (do lô bắn sau mỗi lần tìm kiếm sinh
# ra); impression kiểu cũ thì không. Mốc cắt = impression thật ĐẦU TIÊN.
#
# Không có mốc cắt này thì tử số (click lịch sử, tích lũy từ chế độ cũ) và mẫu số
# (impression mới) nằm ở HAI thang đo khác nhau vô thời hạn: ngay sau migration
# mọi vùng có impressions = 0 nhưng clicks còn nguyên, cho CTR = (clicks+1)/10 —
# vượt 1.0 và làm xếp hạng méo NẶNG HƠN trước khi sửa. Kẹp trần ở smoothed_ctr
# chỉ che triệu chứng; đây mới là chỗ chữa nguyên nhân.
#
# Khi chưa có impression thật nào, mốc cắt = NOW() nên không hàng nào lọt: câu
# này trả về RỖNG, không vùng nào được materialize, và `serving.attach_region_ctr`
# gán 0.0 cho mọi vùng — KHÔNG phải prior 0.1. Nói cách khác tín hiệu ctr tắt
# hoàn toàn cho tới khi có impression thật, và đó là hành vi đúng: thà không có
# tín hiệu còn hơn có một tín hiệu bịa.
_REGION_CTR_SQL = """
    WITH cutover AS (
        SELECT COALESCE(MIN(occurred_at), NOW()) AS started_at
        FROM ingestion_events
        WHERE event_type = 'poi_impression'
          AND metadata->>'request_id' IS NOT NULL
    )
    SELECT p.district AS district, p.category AS category,
           COUNT(*) FILTER (WHERE e.event_type = 'poi_impression')::int AS impressions,
           -- Chỉ đếm click CÓ request_id, tức click phát sinh từ một kết quả tìm
           -- kiếm đã được ghi impression. Click từ khối trending hay khối gợi ý
           -- không hề có impression tương ứng, nên nếu đếm chúng thì tử số lại
           -- lớn hơn mẫu số theo một đường khác và CTR vẫn bị thổi phồng — chỉ
           -- là lần này chạm trần 1.0 thay vì vọt lên 4.1.
           COUNT(*) FILTER (
               WHERE e.event_type = 'poi_click'
                 AND e.metadata->>'request_id' IS NOT NULL
           )::int AS clicks
    FROM ingestion_events e
    JOIN pois p ON p.id::text = e.poi_id
    CROSS JOIN cutover c
    WHERE e.event_type IN ('poi_impression', 'poi_click')
      AND e.occurred_at >= c.started_at
      AND e.occurred_at > NOW() - INTERVAL '30 days'
    GROUP BY p.district, p.category
"""

# Prior Beta(1,9) ~ CTR nền 0.1 để 1 click không cho CTR = 1.0.
_CTR_ALPHA = 1.0
_CTR_BETA = 9.0


def _fetch(database_url: str, query: str) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]


def smoothed_ctr(clicks: int, impressions: int) -> float:
    """CTR làm mượt Beta(1,9), kẹp trần 1.0.

    Công thức thô vượt 1.0 khi ``clicks >= impressions + 10`` — ví dụ 40 click /
    0 impression cho 4.1. Giá trị đó được nhân trọng số 0.08 rồi cộng thẳng vào
    điểm xếp hạng (``ranking.py``), tức +0.33 điểm, lớn hơn cả trọng số text
    (0.26) lẫn spatial (0.24). Kẹp trần là lớp chặn cuối; nguyên nhân gốc được
    xử ở ``_REGION_CTR_SQL``.
    """
    if clicks > impressions:
        logger.warning(
            "CTR bất thường: %d click nhưng chỉ %d impression — click và impression "
            "đang lệch thang đo, kiểm tra mốc cắt trong _REGION_CTR_SQL",
            clicks,
            impressions,
        )
    return round(min(1.0, (clicks + _CTR_ALPHA) / (impressions + _CTR_ALPHA + _CTR_BETA)), 6)


def compute_user_profiles(database_url: str | None = None) -> list[dict[str, Any]]:
    rows = _fetch(database_url or DATABASE_URL, _USER_PROFILE_SQL)
    by_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        session = by_session.setdefault(
            row["session_id"],
            {"session_id": row["session_id"], "event_count": 0, "affinity": {}, "_price": []},
        )
        session["event_count"] += row["events"]
        session["affinity"][row["category"]] = row["weight"]
        if row["avg_price"]:
            session["_price"].append(row["avg_price"])

    profiles: list[dict[str, Any]] = []
    for session in by_session.values():
        affinity = session["affinity"]
        max_weight = max(affinity.values()) if affinity else 0.0
        normalized = (
            {cat: round(weight / max_weight, 6) for cat, weight in affinity.items()}
            if max_weight
            else {}
        )
        prices = session.pop("_price")
        profiles.append(
            {
                "session_id": session["session_id"],
                "event_count": session["event_count"],
                "top_category": max(affinity, key=affinity.get) if affinity else None,
                "pref_price_level": round(sum(prices) / len(prices), 3) if prices else None,
                "affinity": normalized,
            }
        )
    return profiles


def compute_poi_embeddings(
    database_url: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """POI embedding đã được tính tất định lúc ingest; feature store chỉ phơi ra
    dưới đúng tên/version để training và serving dùng chung."""
    query = "SELECT id::text AS poi_id, embedding FROM pois WHERE embedding IS NOT NULL"
    if limit:
        query += f" LIMIT {int(limit)}"
    return [
        {"poi_id": row["poi_id"], "embedding": [float(v) for v in row["embedding"]]}
        for row in _fetch(database_url or DATABASE_URL, query)
    ]


def compute_region_ctr(database_url: str | None = None) -> list[dict[str, Any]]:
    rows = _fetch(database_url or DATABASE_URL, _REGION_CTR_SQL)
    features: list[dict[str, Any]] = []
    for row in rows:
        features.append(
            {
                "region": region_key(row["district"], row["category"]),
                "district": row["district"],
                "category": row["category"],
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": smoothed_ctr(row["clicks"], row["impressions"]),
            }
        )
    return features
