"""Ghi lại feature vector THẬT đã dùng để xếp hạng, cho từng (request_id, poi_id).

Dùng đúng ``ltr.features.feature_row`` — cùng hàm ``ltr_model.score()`` gọi lúc
phục vụ — nên feature ghi vào đây KHÔNG THỂ lệch với feature model thật sự
thấy, dù ranker đang chạy là "linear" hay "ltr". Đây chính là cách vá khiếm
khuyết #1 mà ``ltr/dataset.py`` tự nêu cho nguồn nhãn ``click``: "không có ảnh
chụp đặc trưng tại thời điểm hiển thị".

Ghi ở server (``api.contextual_search``), ngay sau khi có ``rank`` cuối cùng —
không phải client, để feature dùng huấn luyện không thể bị giả mạo qua
telemetry.
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .config import settings
from .ltr.features import FEATURE_NAMES, feature_row

DATABASE_URL = settings.database_url

NAN = float("nan")


def _json_safe_features(features: dict[str, float]) -> dict[str, float | None]:
    """``feature_row`` dùng ``NaN`` cho giá trị thiếu (quy ước của ltr/features.py),
    nhưng JSON chuẩn không có NaN — ``json.dumps`` vẫn in ra token ``NaN`` hợp
    lệ theo Python, mà Postgres JSONB từ chối vì đó không phải JSON. Đổi NaN
    thành ``null`` giữ đúng nghĩa "thiếu dữ liệu" mà JSON hiểu được."""
    return {
        key: (None if isinstance(value, float) and math.isnan(value) else value)
        for key, value in features.items()
    }


def record_snapshot(
    request_id: str | UUID,
    session_id: str | UUID | None,
    retrieval_backend: str,
    results: list[dict[str, Any]],
    category_boost: dict[str, float] | None = None,
    graph_boost: set[str] | frozenset[str] | None = None,
    database_url: str | None = None,
) -> int:
    """Ghi một dòng snapshot cho mỗi POI trong ``results`` (đã có "rank").

    ``category_boost``/``graph_boost`` phải là ĐÚNG hai giá trị đã truyền vào
    ``rank_pois_detailed`` cho request này — feature ``category_affinity``/
    ``in_graph`` chỉ đúng khi tính lại với cùng ngữ cảnh cá nhân hóa.

    ON CONFLICT DO NOTHING: request_id có thể được gọi lại (retry mạng phía
    client) — không ghi đè feature đã log, vì đó là feature THẬT tại lần đầu
    tiên user nhìn thấy kết quả, không phải lần gọi lại.
    """
    if not results:
        return 0
    rows = [
        {
            "request_id": str(request_id),
            "poi_id": poi["id"],
            "rank": poi["rank"],
            "session_id": str(session_id) if session_id else None,
            "retrieval_backend": retrieval_backend,
            "ranker_used": poi.get("rankerUsed") or "linear",
            "features": json.dumps(
                _json_safe_features(
                    feature_row(poi, category_boost=category_boost, graph_boost=graph_boost)
                ),
                ensure_ascii=False,
            ),
        }
        for poi in results
    ]
    with psycopg.connect(database_url or DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO ranking_snapshots (
                    request_id, poi_id, rank, session_id, retrieval_backend,
                    ranker_used, features
                ) VALUES (
                    %(request_id)s::uuid, %(poi_id)s, %(rank)s,
                    %(session_id)s::uuid, %(retrieval_backend)s, %(ranker_used)s,
                    %(features)s::jsonb
                )
                ON CONFLICT (request_id, poi_id) DO NOTHING
                """,
                rows,
            )
        connection.commit()
    return len(rows)


def features_for_request(
    request_id: str, database_url: str | None = None
) -> dict[str, dict[str, float | None]]:
    """poi_id -> feature dict (tên -> giá trị, ``None`` = thiếu) đã ghi cho một
    request_id. Rỗng nếu request đó chưa được snapshot (trước migration 0013,
    hoặc rơi vào nhánh lỗi hiếm đã bị nuốt — xem log ``nearby-api``)."""
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT poi_id, features FROM ranking_snapshots WHERE request_id = %(request_id)s::uuid",
                {"request_id": request_id},
            )
            return {row["poi_id"]: row["features"] for row in cursor.fetchall()}


def row_from_snapshot_features(features: dict[str, Any]) -> list[float]:
    """Đổi feature dict đã lưu (JSON, thiếu = ``null``) thành vector float theo
    đúng thứ tự ``FEATURE_NAMES`` — cùng quy ước NaN=thiếu với ``extract_features``.

    Dùng ``.get()`` nên một snapshot ghi TRƯỚC khi thêm feature mới (ví dụ
    ``vector_score`` thêm sau) tự động coi cột đó là thiếu, không cần migrate
    dữ liệu cũ."""
    return [
        NAN if features.get(name) is None else float(features[name]) for name in FEATURE_NAMES
    ]
