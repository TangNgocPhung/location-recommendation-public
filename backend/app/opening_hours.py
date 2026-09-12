"""Parse a useful, conservative subset of OpenStreetMap opening_hours.

Unsupported expressions are preserved as ``raw`` and evaluate to ``None``
instead of guessing. The importer can therefore be rerun later with a fuller
parser without losing source data.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DAY_INDEX = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
ALL_DAYS = "Mo,Tu,We,Th,Fr,Sa,Su"

# Tiền tố ngày là TÙY CHỌN. Trong cú pháp opening_hours của OSM, một khoảng
# giờ trần như "06:00-22:00" nghĩa là áp dụng MỌI NGÀY.
#
# Trước đây nhóm ngày là bắt buộc, nên 73 POI trong database bị đánh dấu
# "unsupported" chỉ vì ghi giờ theo cách phổ biến nhất. Hậu quả: `is_open_now`
# trả None cho chúng, `contextScore` tụt về mức trung tính, và đặc trưng
# `is_open` của mô hình LTR mất thêm một phần độ phủ vốn đã rất thấp.
PERIOD_PATTERN = re.compile(
    r"^(?:(?P<days>(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?"
    r"(?:,(?:Mo|Tu|We|Th|Fr|Sa|Su))*)\s+)?"
    r"(?P<opens>[0-2]\d:[0-5]\d)-(?P<closes>[0-2]\d:[0-5]\d)$"
)


def _tidy(segment: str) -> str:
    """Chuẩn hóa các biến thể viết tay vô hại trước khi so khớp.

    Dữ liệu OSM do người nhập nên rất hay có "10:00 - 22:00" (thừa dấu cách)
    hoặc "6:00-17:00" (giờ một chữ số). Hai dạng này mang đúng thông tin như
    dạng chuẩn, từ chối chúng chỉ làm mất dữ liệu chứ không tăng độ chính xác.

    CỐ Ý không đụng tới dấu phẩy: trong cú pháp OSM dấu phẩy vừa ngăn cách
    danh sách ngày ("Mo,We 08:00-17:00") vừa ngăn cách nhiều khoảng giờ, nên
    tách bừa sẽ hiểu sai. Những chuỗi đó vẫn để `unsupported`.
    """
    tidied = re.sub(r"\s*-\s*", "-", segment.strip())
    # Đệm 0 cho giờ một chữ số: "6:00" -> "06:00".
    return re.sub(r"(?<![\d:])(\d):(\d{2})", lambda m: f"0{m.group(1)}:{m.group(2)}", tidied)


def _expand_days(value: str) -> list[int]:
    result: set[int] = set()
    for item in value.split(","):
        if "-" not in item:
            result.add(DAY_INDEX[item])
            continue
        start_name, end_name = item.split("-", 1)
        start, end = DAY_INDEX[start_name], DAY_INDEX[end_name]
        day = start
        while True:
            result.add(day)
            if day == end:
                break
            day = (day + 1) % 7
    return sorted(result)


def parse_opening_hours(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = raw.strip()
    if value == "24/7":
        return {"raw": value, "alwaysOpen": True, "periods": []}

    periods: list[dict[str, Any]] = []
    for segment in (part.strip() for part in value.split(";")):
        match = PERIOD_PATTERN.fullmatch(_tidy(segment))
        if not match:
            return {"raw": value, "parseStatus": "unsupported", "periods": []}
        opens, closes = match.group("opens"), match.group("closes")
        # "24:00" trong OSM là nửa đêm cuối ngày, không phải giờ thứ 24.
        # `time.fromisoformat` không nhận nó, nên quy về "00:00" — logic qua
        # nửa đêm sẵn có ở `is_open_now` xử lý đúng trường hợp này.
        if closes == "24:00":
            closes = "00:00"
        if int(opens[:2]) > 23 or int(closes[:2]) > 23:
            return {"raw": value, "parseStatus": "unsupported", "periods": []}
        if opens == closes:
            # "00:00-24:00" và "00:00-00:00" đều nghĩa là mở suốt; để nguyên
            # thì logic qua nửa đêm sẽ coi là không bao giờ mở.
            return {"raw": value, "alwaysOpen": True, "periods": []}
        periods.append(
            {
                # Không có tiền tố ngày = mọi ngày trong tuần.
                "days": _expand_days(match.group("days") or ALL_DAYS),
                "opens": opens,
                "closes": closes,
            }
        )
    return {"raw": value, "parseStatus": "parsed", "periods": periods}


def is_open_now(
    schedule: dict[str, Any] | None,
    timezone_name: str,
    at: datetime | None = None,
) -> bool | None:
    if not schedule:
        return None
    if schedule.get("alwaysOpen"):
        return True
    if schedule.get("parseStatus") != "parsed":
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None
    local = (at or datetime.now(zone)).astimezone(zone)
    current = local.time().replace(tzinfo=None)
    weekday = local.weekday()

    for period in schedule.get("periods", []):
        opens = time.fromisoformat(period["opens"])
        closes = time.fromisoformat(period["closes"])
        days = period["days"]
        if opens <= closes:
            if weekday in days and opens <= current < closes:
                return True
        else:
            if weekday in days and current >= opens:
                return True
            if (weekday - 1) % 7 in days and current < closes:
                return True
    return False


def _concrete_intervals(
    schedule: dict[str, Any], zone: ZoneInfo, reference: datetime
) -> list[tuple[datetime, datetime]]:
    """Trải các period thành các khoảng mở cụ thể quanh ``reference`` (hôm qua →
    ngày mai) để xử lý cả ca qua đêm một cách nhất quán."""
    intervals: list[tuple[datetime, datetime]] = []
    base = reference.date()
    for offset in (-1, 0, 1):
        day = base + timedelta(days=offset)
        weekday = day.weekday()
        for period in schedule.get("periods", []):
            if weekday not in period["days"]:
                continue
            opens = time.fromisoformat(period["opens"])
            closes = time.fromisoformat(period["closes"])
            start = datetime.combine(day, opens, tzinfo=zone)
            end_day = day if closes > opens else day + timedelta(days=1)
            end = datetime.combine(end_day, closes, tzinfo=zone)
            intervals.append((start, end))
    return intervals


def opening_status(
    schedule: dict[str, Any] | None,
    timezone_name: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Trạng thái giờ mở giàu hơn ``is_open_now``.

    Trả về ``openNow`` (bool | None), ``closesInMinutes`` (số phút còn lại đến
    giờ đóng nếu đang mở) và ``opensInMinutes`` (số phút đến lần mở kế nếu đang
    đóng). ``None`` nghĩa là không đủ dữ liệu để kết luận (giờ chưa parse được).
    """
    result: dict[str, Any] = {
        "openNow": None,
        "closesInMinutes": None,
        "opensInMinutes": None,
    }
    if not schedule:
        return result
    if schedule.get("alwaysOpen"):
        result["openNow"] = True
        return result
    if schedule.get("parseStatus") != "parsed":
        return result
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return result

    now = (at or datetime.now(zone)).astimezone(zone)
    intervals = _concrete_intervals(schedule, zone, now)
    if not intervals:
        result["openNow"] = False
        return result

    for start, end in intervals:
        if start <= now < end:
            result["openNow"] = True
            result["closesInMinutes"] = int((end - now).total_seconds() // 60)
            return result

    result["openNow"] = False
    future_starts = sorted(start for start, _ in intervals if start > now)
    if future_starts:
        result["opensInMinutes"] = int((future_starts[0] - now).total_seconds() // 60)
    return result
