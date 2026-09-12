from datetime import datetime
from zoneinfo import ZoneInfo

from app.opening_hours import opening_status, parse_opening_hours

TZ = "Asia/Ho_Chi_Minh"
zone = ZoneInfo(TZ)


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=zone)


def test_open_now_reports_minutes_until_close():
    schedule = parse_opening_hours("Mo-Su 08:00-22:00")
    status = opening_status(schedule, TZ, at(2026, 9, 7, 21, 30))  # Monday 21:30

    assert status["openNow"] is True
    assert status["closesInMinutes"] == 30
    assert status["opensInMinutes"] is None


def test_closed_before_opening_reports_minutes_until_open():
    schedule = parse_opening_hours("Mo-Su 08:00-22:00")
    status = opening_status(schedule, TZ, at(2026, 9, 7, 7, 0))  # Monday 07:00

    assert status["openNow"] is False
    assert status["opensInMinutes"] == 60


def test_overnight_period_is_open_after_midnight():
    schedule = parse_opening_hours("Mo-Su 20:00-02:00")
    status = opening_status(schedule, TZ, at(2026, 9, 8, 1, 0))  # Tue 01:00 (Mo night)

    assert status["openNow"] is True
    assert status["closesInMinutes"] == 60


def test_always_open_has_no_close_countdown():
    status = opening_status(parse_opening_hours("24/7"), TZ, at(2026, 9, 7, 3, 0))
    assert status["openNow"] is True
    assert status["closesInMinutes"] is None


def test_unparsed_schedule_is_unknown():
    status = opening_status(parse_opening_hours("by appointment"), TZ, at(2026, 9, 7, 10))
    assert status == {"openNow": None, "closesInMinutes": None, "opensInMinutes": None}


def test_day_scoped_period_closed_on_other_day():
    schedule = parse_opening_hours("Sa-Su 09:00-18:00")
    status = opening_status(schedule, TZ, at(2026, 9, 7, 12, 0))  # Monday
    assert status["openNow"] is False
