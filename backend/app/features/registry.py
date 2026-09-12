"""Định nghĩa feature view có version — "hợp đồng" dùng chung giữa offline
(huấn luyện) và online (serving) để tránh training/serving skew.

Thuần dữ liệu, không I/O, để test và để cả hai phía tham chiếu cùng một nguồn.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeatureView:
    name: str
    version: str
    entity: str  # khóa tra cứu: session_id | region | poi_id
    features: tuple[str, ...]
    source: str  # nơi tính offline (bảng/nguồn)
    online_prefix: str = field(default="")

    def online_key(self, entity_id: str) -> str:
        """Khóa Redis cho một entity ở online store."""
        prefix = self.online_prefix or f"feat:{self.version}:{self.name}"
        return f"{prefix}:{entity_id}"


USER_PROFILE = FeatureView(
    name="user_profile",
    version="v1",
    entity="session_id",
    features=("event_count", "top_category", "pref_price_level", "affinity"),
    source="ingestion_events + pois",
)

REGION_CTR = FeatureView(
    # v2: trước bước A2, impression là tập con của click nên CTR đã materialize
    # (có vùng lên tới 0.833) là rác. online_key sinh khóa Redis theo version,
    # nên giữ v1 sẽ để giá trị hỏng và giá trị mới chung một namespace, ghi đè
    # lẫn lộn theo thứ tự materialize và không rollback được.
    # SAU KHI BUMP PHẢI CHẠY LẠI materialize, nếu không mọi vùng đọc ra 0.0.
    name="region_ctr",
    version="v2",
    entity="region",  # "<district>|<category>"
    features=("ctr", "clicks", "impressions"),
    source="ingestion_events + pois",
)

POI_EMBEDDING = FeatureView(
    name="poi_embedding",
    # phải trùng poi_features.EMBEDDING_MODEL — test_features_registry khóa điều này
    version="hashing-v2-64",
    entity="poi_id",
    features=("embedding",),
    source="pois.embedding",  # đã tính sẵn khi ingest POI
)

REGISTRY: dict[str, FeatureView] = {
    view.name: view for view in (USER_PROFILE, REGION_CTR, POI_EMBEDDING)
}


def get_view(name: str) -> FeatureView:
    return REGISTRY[name]


def region_key(district: str | None, category: str | None) -> str:
    """Chuẩn hóa khóa vùng cho region_ctr."""
    return f"{(district or 'unknown').strip()}|{(category or 'unknown').strip()}"
