"""Phase 4 — dựng tập huấn luyện cho Learning-to-Rank.

Hai nguồn nhãn, cố ý tách bạch vì chúng có độ tin cậy rất khác nhau:

## 1. ``judgment`` — phán quyết liên quan do người gán

Đọc từ ``tests/fixtures/relevance_judgments.json``, thang 4 mức của
``docs/quality-gates.md`` (3 hoàn hảo / 2 liên quan / 1 chấp nhận được / 0
không liên quan). Ứng viên được lấy bằng chính đường truy xuất đang chạy, rồi
làm giàu bằng chính pipeline đang chạy — nên đặc trưng khớp tuyệt đối với lúc
phục vụ.

Đây là nguồn nhãn CHÍNH. Nhãn không phụ thuộc thời điểm nên tính lại đặc trưng
hôm nay không gây lệch, và nó không mang position bias.

## 2. ``click`` — suy ra từ log hành vi

Với mỗi ``request_id``, tập ứng viên là các POI đã được ghi ``poi_impression``,
nhãn là 1 nếu có ``poi_click`` cùng ``request_id`` + ``poi_id``, ngược lại 0.

Nguồn này có BA khiếm khuyết đã đo được, phải nêu trong báo cáo:

1. **Không có ảnh chụp đặc trưng tại thời điểm hiển thị — ĐÃ VÁ (xem
   ``app/ranking_snapshots.py``, migration 0013).** Từ nay, mỗi request được
   snapshot đúng ``feature_row()`` ngay lúc phục vụ; nhóm nào có snapshot thì
   ``build_from_clicks`` đọc thẳng feature đó thay vì tính lại — các tín hiệu
   phụ thuộc thời gian (``recency``, ``trending``, ``is_open``, ``hour_of_day``)
   giờ ĐÚNG là giá trị người dùng thực sự đã thấy. Request từ TRƯỚC migration
   0013 không có snapshot, nên vẫn rơi về đường tính lại cũ (``_pipeline_candidates``)
   — ``Group.feature_source`` ghi rõ nhóm nào đi đường nào, xem ``Dataset.summary()``.
2. **Position bias.** Người dùng click cái nằm trên, không nhất thiết là cái
   liên quan nhất. Không khử bias (không randomization, không propensity) thì
   mô hình học lại chính thứ tự cũ.
3. **Tọa độ tâm tìm kiếm không nằm trong sự kiện impression.** Phải suy ra từ
   sự kiện ``search`` gần nhất cùng phiên có toạ độ; nhóm nào không suy ra được
   thì bỏ.

Trên dữ liệu hiện có (đo 2026-09-11): 36 nhóm request có impression, **chỉ 6
nhóm có click**, và chỉ 14 nhóm suy ra được tâm tìm kiếm. Nhóm không có ứng
viên liên quan nào bị ``lambdarank`` bỏ qua, nên tập click hiện tại KHÔNG đủ để
huấn luyện. Hàm vẫn được viết đầy đủ để chạy được ngay khi log đủ lớn.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from ..config import settings
from ..features.serving import attach_region_ctr
from ..ranking import apply_trending_boost, retrieve_candidates
from ..ranking_snapshots import features_for_request, row_from_snapshot_features
from ..spatio_temporal import enrich_candidates
from .features import FEATURE_NAMES, coverage, extract_features

logger = logging.getLogger("nearby-ltr")

DATABASE_URL = settings.database_url
CANDIDATE_POOL = 100


@dataclass
class Group:
    """Một truy vấn và toàn bộ ứng viên của nó — đơn vị nhóm của lambdarank."""

    qid: str
    query: str
    latitude: float
    longitude: float
    radius: int
    source: str  # "judgment" | "click"
    poi_ids: list[str] = field(default_factory=list)
    rows: list[list[float]] = field(default_factory=list)
    labels: list[float] = field(default_factory=list)
    # "snapshot" = feature đọc từ ranking_snapshots (đúng thời điểm serve),
    # "recomputed" = tính lại bằng pipeline hiện tại (request từ trước migration
    # 0013, hoặc luôn luôn với nguồn "judgment" — nhãn đó không phụ thuộc thời
    # gian nên tính lại không gây lệch, xem docstring đầu module).
    feature_source: str = "recomputed"

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def n_positive(self) -> int:
        return sum(1 for label in self.labels if label > 0)


@dataclass
class Dataset:
    groups: list[Group] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def usable_groups(self) -> list[Group]:
        """Nhóm dùng được cho lambdarank: phải có ít nhất một ứng viên liên
        quan, nếu không thì không có cặp nào để so sánh và nhóm bị bỏ qua."""
        return [group for group in self.groups if group.n_positive > 0]

    def summary(self) -> dict[str, Any]:
        usable = self.usable_groups
        rows = [row for group in usable for row in group.rows]
        all_rows = [row for group in self.groups for row in group.rows]
        all_labels = [label for group in self.groups for label in group.labels]
        group_sizes = [len(group) for group in self.groups]
        click_rows_total = sum(len(group) for group in self.groups if group.source == "click")
        click_rows_snapshot = sum(
            len(group)
            for group in self.groups
            if group.source == "click" and group.feature_source == "snapshot"
        )
        return {
            "groups": len(self.groups),
            "usableGroups": len(usable),
            "rows": sum(len(group) for group in self.groups),
            "usableRows": len(rows),
            "positives": sum(group.n_positive for group in self.groups),
            # positives / tổng rows — không phải / usableRows, để phản ánh đúng
            # tỉ lệ thật trong toàn bộ log (bao gồm cả nhóm bị lambdarank bỏ vì
            # không có positive nào, xem `usable_groups`).
            "positiveRate": (
                round(sum(1 for label in all_labels if label > 0) / len(all_labels), 4)
                if all_labels
                else 0.0
            ),
            "labelDistribution": {
                str(label): all_labels.count(label) for label in sorted(set(all_labels))
            },
            "groupSize": {
                "min": min(group_sizes) if group_sizes else 0,
                "max": max(group_sizes) if group_sizes else 0,
                "mean": round(sum(group_sizes) / len(group_sizes), 2) if group_sizes else 0.0,
            },
            # Nhóm chỉ 1 POI không có gì để lambdarank so sánh cặp — vẫn đếm
            # vào "usableGroups" (n_positive > 0 vẫn đúng với 1 POI), nhưng
            # KHÔNG đóng góp tín hiệu thứ hạng nào. "groups" lớn mà phần lớn
            # chỉ có 1 POI thì con số đó gây hiểu lầm, nên tách riêng ra đây.
            "groupsWithGe2Pois": sum(1 for size in group_sizes if size >= 2),
            "groupsWith1Poi": sum(1 for size in group_sizes if size == 1),
            "skipped": len(self.skipped),
            "bySource": {
                source: sum(1 for group in self.groups if group.source == source)
                for source in sorted({group.source for group in self.groups})
            },
            # Bao nhiêu nhóm "click" lấy được feature THẬT từ ranking_snapshots
            # so với phải tính lại (request từ trước migration 0013). Con số
            # này PHẢI xuất hiện trong báo cáo — im lặng thì không ai biết
            # nhãn click đang dùng feature đúng lúc serve hay chỉ là ước lượng.
            "clickFeatureSource": {
                fs: sum(1 for group in self.groups if group.source == "click" and group.feature_source == fs)
                for fs in ("snapshot", "recomputed")
            },
            # Cùng số liệu trên nhưng tính theo DÒNG chứ không theo NHÓM — một
            # nhóm lớn lệch có thể che mất một nhóm nhỏ toàn recomputed.
            "clickSnapshotRowCoverage": (
                round(click_rows_snapshot / click_rows_total, 4) if click_rows_total else None
            ),
            "featureCoverage": coverage(rows),
        }

    def to_jsonl(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with path.open("w", encoding="utf-8") as handle:
            for group in self.groups:
                for poi_id, row, label in zip(group.poi_ids, group.rows, group.labels):
                    handle.write(
                        json.dumps(
                            {
                                "qid": group.qid,
                                "query": group.query,
                                "source": group.source,
                                "featureSource": group.feature_source,
                                "poi_id": poi_id,
                                "label": label,
                                "features": dict(zip(FEATURE_NAMES, row)),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    written += 1
        return written


def _pipeline_candidates(
    latitude: float, longitude: float, radius: int, query: str | None
) -> list[dict[str, Any]]:
    """Chạy đúng chuỗi làm giàu của đường phục vụ, dừng ngay TRƯỚC ``rerank``.

    Phải đi qua cả ba bước: thiếu bước nào thì đặc trưng tương ứng thành NaN ở
    tập huấn luyện trong khi lúc phục vụ lại có giá trị — đúng kiểu lệch
    train/serve mà `features.py` được viết để tránh.
    """
    candidates, _backend = retrieve_candidates(
        latitude, longitude, radius, query, None, CANDIDATE_POOL
    )
    candidates = apply_trending_boost(candidates)
    candidates = enrich_candidates(candidates)
    candidates = attach_region_ctr(candidates)
    return candidates


def build_from_judgments(path: Path) -> Dataset:
    """Nguồn nhãn chính: phán quyết liên quan do người gán, thang 0–3."""
    cases = json.loads(path.read_text(encoding="utf-8"))
    dataset = Dataset()
    for index, case in enumerate(cases):
        relevance = {str(k): float(v) for k, v in (case.get("relevance") or {}).items()}
        candidates = _pipeline_candidates(
            case["latitude"], case["longitude"], case["radius"], case["query"]
        )
        if not candidates:
            dataset.skipped.append({"qid": f"j{index}", "reason": "không có ứng viên"})
            continue
        group = Group(
            qid=f"j{index}",
            query=case["query"],
            latitude=case["latitude"],
            longitude=case["longitude"],
            radius=case["radius"],
            source="judgment",
        )
        for candidate in candidates:
            group.poi_ids.append(candidate["id"])
            group.rows.append(extract_features(candidate))
            # Ứng viên không nằm trong phán quyết = không liên quan (0). Đúng
            # với cách `evaluation.ndcg_at_k` đọc cùng cấu trúc nhãn này.
            group.labels.append(relevance.get(candidate["id"], 0.0))
        dataset.groups.append(group)
    return dataset


_CLICK_GROUPS_SQL = """
WITH impressions AS (
    SELECT metadata->>'request_id' AS request_id,
           session_id,
           MIN(occurred_at) AS shown_at,
           MIN(metadata->>'query') AS query,
           array_agg(DISTINCT poi_id) AS poi_ids
    FROM ingestion_events
    WHERE event_type = 'poi_impression'
      AND metadata ? 'request_id'
      AND poi_id IS NOT NULL
    GROUP BY 1, 2
),
clicks AS (
    SELECT metadata->>'request_id' AS request_id,
           array_agg(DISTINCT poi_id) AS clicked_ids
    FROM ingestion_events
    WHERE event_type IN ('poi_click', 'navigation_start')
      AND metadata ? 'request_id'
      AND poi_id IS NOT NULL
    GROUP BY 1
)
SELECT i.request_id,
       i.query,
       i.poi_ids,
       COALESCE(c.clicked_ids, ARRAY[]::text[]) AS clicked_ids,
       centre.latitude,
       centre.longitude
FROM impressions i
LEFT JOIN clicks c ON c.request_id = i.request_id
LEFT JOIN LATERAL (
    -- Tâm tìm kiếm không có trong sự kiện impression; lấy từ sự kiện `search`
    -- gần nhất cùng phiên, cho phép trễ 5 giây vì client gửi theo lô.
    SELECT ST_Y(s.location::geometry) AS latitude,
           ST_X(s.location::geometry) AS longitude
    FROM ingestion_events s
    WHERE s.event_type = 'search'
      AND s.session_id = i.session_id
      AND s.location IS NOT NULL
      AND s.occurred_at <= i.shown_at + INTERVAL '5 seconds'
    ORDER BY s.occurred_at DESC
    LIMIT 1
) AS centre ON TRUE
ORDER BY i.shown_at
"""


def build_from_clicks(radius: int = 5_000, database_url: str | None = None) -> Dataset:
    """Nguồn nhãn phụ: click nhị phân suy ra từ log hiển thị.

    Đọc kỹ ba khiếm khuyết ở docstring đầu module trước khi dùng số liệu từ đây
    cho bất kỳ kết luận nào.
    """
    dataset = Dataset()
    with psycopg.connect(database_url or DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(_CLICK_GROUPS_SQL)
            records = cursor.fetchall()

    for record in records:
        request_id = record["request_id"]
        if record["latitude"] is None:
            dataset.skipped.append(
                {"qid": request_id, "reason": "không suy ra được tâm tìm kiếm"}
            )
            continue
        clicked = set(record["clicked_ids"] or [])
        impressed = set(record["poi_ids"] or [])
        if not clicked:
            dataset.skipped.append({"qid": request_id, "reason": "không có click"})
            continue

        group = Group(
            qid=request_id,
            query=record["query"] or "",
            latitude=record["latitude"],
            longitude=record["longitude"],
            radius=radius,
            source="click",
        )

        # Ưu tiên feature ĐÚNG THỜI ĐIỂM SERVE từ ranking_snapshots (migration
        # 0013). Chỉ request từ TRƯỚC migration đó (hoặc rơi vào nhánh lỗi ghi
        # snapshot hiếm gặp) mới không có gì ở đây và phải rơi về tính lại.
        snapshot_map = features_for_request(request_id, database_url=database_url)
        # Cùng lý do với đường tính lại cũ: chỉ giữ POI NGƯỜI DÙNG THỰC SỰ ĐÃ
        # THẤY. Giao với `impressed` đề phòng lệch hiếm gặp giữa snapshot server
        # và impression client (ví dụ client rớt mạng giữa batch).
        snapshot_ids = [poi_id for poi_id in impressed if poi_id in snapshot_map]

        if snapshot_ids:
            group.feature_source = "snapshot"
            for poi_id in snapshot_ids:
                group.poi_ids.append(poi_id)
                group.rows.append(row_from_snapshot_features(snapshot_map[poi_id]))
                group.labels.append(1.0 if poi_id in clicked else 0.0)
            dataset.groups.append(group)
            continue

        candidates = _pipeline_candidates(
            record["latitude"], record["longitude"], radius, record["query"]
        )
        # Chỉ giữ những POI NGƯỜI DÙNG THỰC SỰ ĐÃ THẤY. Chấm điểm cả những POI
        # chưa từng hiển thị là sai: nhãn 0 của chúng nghĩa là "không có cơ hội
        # được click", không phải "được thấy rồi bỏ qua".
        candidates = [c for c in candidates if c["id"] in impressed]
        if not candidates:
            dataset.skipped.append(
                {"qid": request_id, "reason": "không khớp lại được ứng viên đã hiển thị"}
            )
            continue

        group.feature_source = "recomputed"
        for candidate in candidates:
            group.poi_ids.append(candidate["id"])
            group.rows.append(extract_features(candidate))
            group.labels.append(1.0 if candidate["id"] in clicked else 0.0)
        dataset.groups.append(group)
    return dataset


def merge(datasets: Iterable[Dataset]) -> Dataset:
    merged = Dataset()
    for dataset in datasets:
        merged.groups.extend(dataset.groups)
        merged.skipped.extend(dataset.skipped)
    return merged
