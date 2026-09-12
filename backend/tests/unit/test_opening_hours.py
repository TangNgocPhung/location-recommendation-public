from datetime import datetime
from zoneinfo import ZoneInfo

from app.opening_hours import is_open_now, parse_opening_hours


def test_parses_weekday_ranges_and_evaluates_local_time() -> None:
    schedule = parse_opening_hours("Mo-Fr 07:00-18:00; Sa-Su 08:00-12:00")
    wednesday = datetime(2026, 9, 9, 10, 30, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert schedule["parseStatus"] == "parsed"
    assert is_open_now(schedule, "Asia/Ho_Chi_Minh", wednesday) is True


def test_unsupported_expression_is_preserved_without_guessing() -> None:
    schedule = parse_opening_hours("sunrise-sunset")
    assert schedule["raw"] == "sunrise-sunset"
    assert schedule["parseStatus"] == "unsupported"
    assert is_open_now(schedule, "Asia/Ho_Chi_Minh") is None


def test_overnight_period_carries_into_next_day() -> None:
    schedule = parse_opening_hours("Mo-Su 18:00-02:00")
    after_midnight = datetime(2026, 9, 9, 1, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert is_open_now(schedule, "Asia/Ho_Chi_Minh", after_midnight) is True
