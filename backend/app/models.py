from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class Coordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0, le=100_000)


EventType = Literal[
    "search",
    "location_ping",
    "poi_impression",  # POI đã được HIỂN THỊ trong kết quả (metadata: request_id, query, rank)
    "poi_dwell",  # thời gian ở lại một POI đã click (dwell_ms)
    "poi_click",
    "navigation_start",
    "review",
]


class ClientEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    session_id: UUID
    user_id: str | None = Field(default=None, max_length=120)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    location: Coordinates | None = None
    location_consent: bool = False
    poi_id: str | None = Field(default=None, max_length=128)
    query: str | None = Field(default=None, max_length=160)
    rating: int | None = Field(default=None, ge=1, le=5)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event_shape(self) -> "ClientEvent":
        if self.event_type == "location_ping" and not self.location:
            raise ValueError("location_ping requires location")
        if self.location and not self.location_consent:
            raise ValueError("location coordinates require explicit consent")
        if self.event_type in {
            "poi_click",
            "poi_impression",
            "poi_dwell",
            "navigation_start",
            "review",
        } and not self.poi_id:
            raise ValueError(f"{self.event_type} requires poi_id")
        if self.event_type == "review" and self.rating is None:
            raise ValueError("review requires rating")
        return self


class EventBatch(BaseModel):
    events: list[ClientEvent] = Field(min_length=1, max_length=50)


class GeoParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=160)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius: int = Field(default=3_000, ge=100, le=50_000)
    limit: int = Field(default=50, ge=1, le=100)
    session_id: UUID | None = None
    category: str | None = Field(default=None, max_length=64)
    # "linear" = tổ hợp trọng số tay (baseline), "ltr" = LambdaMART.
    # Chọn ở cấp request để Phase 7 đo được hai bộ xếp hạng trên CÙNG một
    # tiến trình, cùng cache, cùng dữ liệu — nếu phải khởi động lại server
    # giữa hai lần đo thì con số độ trễ không so sánh được.
    ranker: Literal["linear", "ltr"] = "linear"


class ReviewRequest(BaseModel):
    """Đánh giá 1-5 sao thật — explicit feedback, khác ClientEvent event_type
    'review' (tín hiệu nhẹ trong ingestion_events, dùng để tính category
    affinity). Cái này ghi vào `poi_reviews`, hiển thị công khai, và aggregate
    ngược vào `pois.rating`/`review_count`."""

    session_id: UUID | None = None
    rating: int = Field(ge=1, le=5)
    author_name: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=160)
    body: str | None = Field(default=None, max_length=2_000)


class GeofenceRequest(BaseModel):
    """Đăng ký "nhắc tôi khi tới gần" cho một POI.

    KHÔNG nhận toạ độ từ client: tâm vùng lấy thẳng từ ``pois.location`` trong
    chính câu lệnh chèn. Nhận toạ độ ở đây thì một lỗi phía giao diện sẽ đặt
    vùng nhắc ở sai chỗ mà không có gì phát hiện được.
    """

    poi_id: UUID
    session_id: UUID | None = None
    radius_meters: int = Field(default=300, ge=50, le=50_000)
    label: str | None = Field(default=None, max_length=160)
    notify_only_when_open: bool = False
    # Chống báo trùng: đứng yên trong vùng, mỗi 20 giây một ping, sẽ là mỗi 20
    # giây một thông báo nếu không có khoảng lặng này.
    cooldown_minutes: int = Field(default=30, ge=1, le=1_440)
