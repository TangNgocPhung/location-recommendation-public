"""Giờ mở cửa ghi không kèm ngày — dạng phổ biến nhất trong dữ liệu OSM thật.

Đo trên database: 73/505 POI có `opening_hours` từng bị đánh dấu
"unsupported" chỉ vì viết "06:00-22:00" thay vì "Mo-Su 06:00-22:00". Chúng
mất hẳn tín hiệu `is_open`, kéo theo `contextScore` về mức trung tính và làm
giảm độ phủ của đặc trưng `is_open` trong mô hình LTR.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.opening_hours import is_open_now, parse_opening_hours

TZ = "Asia/Ho_Chi_Minh"


def at(hour: int, minute: int = 0, weekday: int = 0) -> datetime:
    # 2026-09-07 là thứ Hai; cộng thêm để chọn đúng thứ trong tuần.
    return datetime(2026, 9, 7 + weekday, hour, minute, tzinfo=ZoneInfo(TZ))


class TestKhoangGioKhongKemNgay:
    def test_parse_duoc(self):
        schedule = parse_opening_hours("06:00-22:00")
        assert schedule["parseStatus"] == "parsed"
        assert len(schedule["periods"]) == 1

    def test_ap_dung_cho_moi_ngay_trong_tuan(self):
        schedule = parse_opening_hours("06:00-22:00")
        assert schedule["periods"][0]["days"] == [0, 1, 2, 3, 4, 5, 6]

    def test_mo_trong_gio_ke_ca_chu_nhat(self):
        schedule = parse_opening_hours("06:00-22:00")
        assert is_open_now(schedule, TZ, at(10, weekday=6)) is True

    def test_dong_ngoai_gio(self):
        schedule = parse_opening_hours("06:00-22:00")
        assert is_open_now(schedule, TZ, at(23)) is False
        assert is_open_now(schedule, TZ, at(5)) is False

    def test_van_nhan_dang_co_ngay_nhu_cu(self):
        schedule = parse_opening_hours("Mo-Fr 08:00-17:00")
        assert schedule["periods"][0]["days"] == [0, 1, 2, 3, 4]
        assert is_open_now(schedule, TZ, at(10, weekday=5)) is False


class TestGio24:
    def test_24_00_la_nua_dem_cuoi_ngay(self):
        schedule = parse_opening_hours("06:00-24:00")
        assert schedule["parseStatus"] == "parsed"
        assert is_open_now(schedule, TZ, at(23, 30)) is True
        assert is_open_now(schedule, TZ, at(5)) is False

    def test_00_00_24_00_la_mo_suot(self):
        schedule = parse_opening_hours("00:00-24:00")
        assert schedule.get("alwaysOpen") is True
        assert is_open_now(schedule, TZ, at(3)) is True


class TestVanGiuNguyenCaiKhongHieu:
    def test_chi_co_gio_mo_khong_co_gio_dong(self):
        """"06:30" một mình thật sự mơ hồ — phải để unsupported chứ không đoán."""
        assert parse_opening_hours("06:30")["parseStatus"] == "unsupported"

    def test_24_7_van_la_mo_suot(self):
        assert parse_opening_hours("24/7")["alwaysOpen"] is True
