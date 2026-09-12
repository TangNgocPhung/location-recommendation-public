"""Bảo vệ các bất biến của bước A2 (impression thật + poi_dwell).

Trước bước này, `poi_impression` thực chất là sự kiện RỜI một POI đã click, nên
impression là tập con của click và CTR trong feature store tiến tới 1.0. Các
test dưới đây khóa lại từng bất biến đã bị vi phạm, để lỗi không quay lại âm thầm.
"""

import pytest
from pydantic import ValidationError

from app.features import offline, serving
from app.models import ClientEvent
from app.spatio_temporal import POPULARITY_EVENT_TYPES

SESSION = "11111111-1111-4111-8111-111111111111"


def test_poi_dwell_is_accepted_by_the_event_model():
    event = ClientEvent(event_type="poi_dwell", session_id=SESSION, poi_id="abc", dwell_ms=1_500)
    assert event.event_type == "poi_dwell"


def test_poi_dwell_requires_poi_id():
    # Không phải kiểm tra thừa: poi_id là cột TEXT không NOT NULL, nên nếu model
    # bỏ qua thì hàng vẫn ghi được với poi_id NULL rồi bị MỌI JOIN lặng lẽ bỏ qua.
    with pytest.raises(ValidationError):
        ClientEvent(event_type="poi_dwell", session_id=SESSION, dwell_ms=1_500)


@pytest.mark.parametrize(
    ("clicks", "impressions"),
    [(40, 0), (60, 40), (1, 0), (500, 100)],
)
def test_smoothed_ctr_never_exceeds_one(clicks: int, impressions: int):
    """Công thức thô cho (40+1)/(0+10) = 4.1.

    Nhân trọng số ctr 0.08 thành +0.33 điểm — lớn hơn cả trọng số text (0.26)
    lẫn spatial (0.24), tức xếp hạng méo nặng hơn cả trước khi sửa. Mọi assert
    cũ chỉ thử clicks <= impressions, đúng trường hợp không bao giờ xảy ra với
    dữ liệu thật, nên lỗi sống sót.
    """
    assert 0.0 <= offline.smoothed_ctr(clicks, impressions) <= 1.0


def test_region_ctr_sql_only_counts_events_after_the_first_real_impression():
    """Không có mốc cắt thì tử số (click lịch sử) và mẫu số (impression mới)
    nằm ở hai thang đo khác nhau vô thời hạn."""
    sql = offline._REGION_CTR_SQL
    assert "cutover" in sql
    assert "metadata->>'request_id' IS NOT NULL" in sql
    assert "e.occurred_at >= c.started_at" in sql


def test_region_ctr_sql_only_counts_clicks_that_came_from_a_search():
    """Click từ khối trending hoặc khối gợi ý không có impression tương ứng.

    Đếm chúng vào tử số làm CTR bị thổi phồng theo một đường khác: lần này chạm
    trần 1.0 thay vì vọt lên 4.1, nhưng vẫn là tín hiệu sai.
    """
    sql = offline._REGION_CTR_SQL
    click_clause = sql[sql.index("AS impressions") :]
    assert "e.metadata->>'request_id' IS NOT NULL" in click_clause


def test_attach_region_ctr_clamps_poisoned_values_from_redis(monkeypatch):
    """Redis giữ giá trị của lần materialize TRƯỚC, nên giá trị hỏng vẫn còn đó
    kể cả sau khi tầng offline đã kẹp."""
    monkeypatch.setattr(serving, "get_online_features", lambda *_: {"Quận 1|cafe": {"ctr": 4.1}})
    candidates = serving.attach_region_ctr([{"district": "Quận 1", "category": "cafe"}])
    assert candidates[0]["regionCtr"] == 1.0


def test_popularity_excludes_impressions_to_break_the_feedback_loop():
    """POI xếp cao → được hiển thị → sinh impression → recency cao → xếp cao hơn.
    Trọng số recency là 0.12, lớn hơn cả ctr 0.08."""
    assert "poi_impression" not in POPULARITY_EVENT_TYPES
    assert "poi_dwell" in POPULARITY_EVENT_TYPES
