"""Lọc chất lượng toạ độ GPS (bước B2).

Trước module này, kiểm tra toạ độ duy nhất là ràng buộc biên của Pydantic, nên
fake GPS đi thẳng vào Redis GEO và search_sessions.last_location.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import geo_quality
from app.geo_quality import evaluate, haversine_meters, implied_speed_kmh

BEN_THANH = (10.7757, 106.7009)
NOW = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)


def test_accepts_a_normal_ping_inside_the_service_area():
    verdict = evaluate(*BEN_THANH, accuracy_meters=12.0)
    assert verdict.accepted is True
    assert verdict.low_quality is False
    assert verdict.reason is None


def test_missing_coordinates_are_not_an_error():
    # Nhiều loại sự kiện không kèm vị trí; đó không phải lỗi chất lượng.
    assert evaluate(None, None).accepted is True


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (21.0278, 105.8342),  # Hà Nội
        (10.7757, 100.0),     # cùng vĩ độ, kinh độ ngoài bbox
        (0.0, 0.0),           # Null Island — toạ độ rác kinh điển
    ],
)
def test_rejects_coordinates_outside_the_service_area(latitude, longitude):
    verdict = evaluate(latitude, longitude)
    assert verdict.accepted is False
    assert verdict.reason == "outside_service_area"


def test_poor_accuracy_is_flagged_but_not_rejected():
    """Toạ độ sai số lớn vẫn cho biết người dùng ở khu vực nào, chỉ không đủ tin
    cậy để tính dwell hay bơm vào trending."""
    verdict = evaluate(*BEN_THANH, accuracy_meters=500.0)
    assert verdict.accepted is True
    assert verdict.low_quality is True


def test_rejects_teleport_between_two_pings():
    """Kịch bản demo: hai ping cách nhau 2 giây nhưng 5 km."""
    previous = (10.7757, 106.7009, NOW)
    verdict = evaluate(
        10.8200,
        106.7009,  # ~4,9 km về phía bắc
        accuracy_meters=10.0,
        previous=previous,
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert verdict.accepted is False
    assert verdict.reason == "teleport"
    assert verdict.speed_kmh > geo_quality.MAX_SPEED_KMH


def test_accepts_a_realistic_motorbike_trip():
    """40 km/h trong 60 giây — phải qua, nếu không thì lọc nhầm người dùng thật."""
    previous = (10.7757, 106.7009, NOW)
    verdict = evaluate(
        10.7817,
        106.7009,  # ~667 m
        accuracy_meters=10.0,
        previous=previous,
        occurred_at=NOW + timedelta(seconds=60),
    )
    assert verdict.accepted is True
    assert verdict.speed_kmh < geo_quality.MAX_SPEED_KMH


def test_two_pings_too_close_in_time_do_not_trigger_a_false_alarm():
    """Sai số GPS vài chục mét chia cho 0,2 giây ra hàng trăm km/h — không được
    coi đó là teleport."""
    previous = (10.7757, 106.7009, NOW)
    verdict = evaluate(
        10.7759,
        106.7011,
        accuracy_meters=10.0,
        previous=previous,
        occurred_at=NOW + timedelta(milliseconds=200),
    )
    assert verdict.accepted is True
    assert verdict.speed_kmh is None


def test_implied_speed_returns_none_below_the_minimum_interval():
    assert implied_speed_kmh(10.78, 106.70, 10.7757, 106.7009, 0.2) is None


def test_haversine_matches_a_known_distance():
    # 0,01 độ vĩ độ ~ 1,11 km ở mọi kinh độ.
    meters = haversine_meters(10.7757, 106.7009, 10.7857, 106.7009)
    assert 1_090 < meters < 1_130


def test_metadata_carries_the_reason_for_auditing():
    verdict = evaluate(21.0278, 105.8342)
    metadata = verdict.as_metadata()
    assert metadata["geo_accepted"] is False
    assert metadata["geo_reject_reason"] == "outside_service_area"
