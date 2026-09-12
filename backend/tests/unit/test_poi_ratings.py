"""Kiểm thử bộ lọc khớp địa điểm khi lấy rating từ Google Places.

Không cần API key: toàn bộ phần dễ sai nhất — quyết định "kết quả Google này
có phải đúng cái POI kia không" — là hàm thuần `evaluate_match`.

Vì sao phải kiểm thử kỹ chỗ này: nhận nhầm một kết quả nghĩa là gán điểm của
cửa hàng NÀY cho cửa hàng KHÁC. Không có ngoại lệ nào được ném, không có dòng
log lỗi nào, và con số sai đó đi thẳng vào bảng kết quả của báo cáo.
"""

from __future__ import annotations

import pytest

from app.poi_ratings import (
    MAX_MATCH_METERS,
    evaluate_match,
    haversine_meters,
    name_similarity,
    normalize_name,
    summarize,
)

# Tạp Hóa Hoàng, Quận 11 — POI thật trong database.
POI = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Tạp Hóa Hoàng",
    "latitude": 10.7650,
    "longitude": 106.6450,
}


def place(name, lat, lng, rating=4.2, count=37, place_id="ChIJ_test"):
    return {
        "id": place_id,
        "displayName": {"text": name},
        "location": {"latitude": lat, "longitude": lng},
        "rating": rating,
        "userRatingCount": count,
    }


class TestChuanHoaTen:
    def test_bo_dau_tieng_viet(self):
        assert normalize_name("Tạp Hóa Hoàng") == ["hoang"]

    def test_bo_chu_d_gach(self):
        assert "duc" in normalize_name("Nhà thờ Đức Bà")

    def test_bo_tu_chi_loai_hinh(self):
        """"Quán Cà Phê Hoàng" và "Cà Phê Hoàng" phải ra cùng một định danh."""
        assert normalize_name("Quán Cà Phê Hoàng") == normalize_name("Cà Phê Hoàng")

    def test_khong_phan_biet_thu_tu_tu(self):
        assert name_similarity("Cà Phê Hoàng", "Hoàng Coffee") == pytest.approx(1.0)

    def test_ten_khac_han_thi_diem_thap(self):
        assert name_similarity("Tạp Hóa Hoàng", "Siêu thị WinMart") < 0.3

    def test_ten_rong_tra_ve_0(self):
        assert name_similarity(None, "Bất kỳ") == 0.0
        assert name_similarity("Quán", "Nhà hàng") == 0.0  # chỉ toàn từ loại hình


class TestKhoangCach:
    def test_haversine_khop_khoang_cach_da_biet(self):
        # Chợ Bến Thành -> Nhà thờ Đức Bà, khoảng 1.0 km.
        metres = haversine_meters(10.7725, 106.6983, 10.7798, 106.6990)
        assert 700 < metres < 1200

    def test_cung_mot_diem_thi_bang_0(self):
        assert haversine_meters(10.77, 106.70, 10.77, 106.70) == pytest.approx(0.0)


class TestQuyetDinhKhop:
    def test_nhan_khi_dung_cho_va_dung_ten(self):
        result = evaluate_match(POI, place("Tạp hóa Hoàng", 10.7651, 106.6451))
        assert result.accepted
        assert result.rating == 4.2
        assert result.review_count == 37
        assert result.place_id == "ChIJ_test"

    def test_tu_choi_khi_o_xa(self):
        """Cùng tên nhưng cách 3 km — gần như chắc chắn là chi nhánh khác."""
        result = evaluate_match(POI, place("Tạp hóa Hoàng", 10.7920, 106.6450))
        assert not result.accepted
        assert "cach" in result.reason
        assert result.distance_meters > MAX_MATCH_METERS

    def test_tu_choi_khi_ten_khac(self):
        """Đúng vị trí nhưng là cửa hàng khác — đây là ca nguy hiểm nhất, vì
        lọc theo khoảng cách một mình sẽ cho nó lọt."""
        result = evaluate_match(POI, place("Cửa hàng Bách Hóa Xanh", 10.7650, 106.6450))
        assert not result.accepted
        assert "ten khac" in result.reason

    def test_tu_choi_khi_google_cung_chua_co_danh_gia(self):
        result = evaluate_match(POI, place("Tạp hóa Hoàng", 10.7651, 106.6451, rating=None))
        assert not result.accepted
        assert result.rating is None
        assert "chua co danh gia" in result.reason

    def test_tu_choi_khi_ket_qua_thieu_toa_do(self):
        broken = {"id": "x", "displayName": {"text": "Tạp hóa Hoàng"}, "rating": 4.5}
        result = evaluate_match(POI, broken)
        assert not result.accepted
        assert "toa do" in result.reason

    def test_lech_toa_do_vua_phai_van_duoc_nhan(self):
        """OSM đặt điểm ở cổng, Google đặt ở tâm nhà — lệch vài chục mét là
        chuyện thường, không được loại."""
        result = evaluate_match(POI, place("Tạp hóa Hoàng", 10.76545, 106.64545))
        assert result.distance_meters < MAX_MATCH_METERS
        assert result.accepted

    def test_khong_bao_gio_nem_loi_voi_du_lieu_thieu(self):
        result = evaluate_match(POI, {})
        assert not result.accepted


class TestTongKet:
    def test_gom_nhom_ly_do_bo_qua(self):
        results = [
            evaluate_match(POI, place("Tạp hóa Hoàng", 10.7651, 106.6451)),
            evaluate_match(POI, place("Tạp hóa Hoàng", 10.7920, 106.6450)),
            evaluate_match(POI, place("Tạp hóa Hoàng", 10.7930, 106.6450)),
        ]
        report = summarize(results)
        assert report["total"] == 3
        assert report["accepted"] == 1
        assert report["acceptRate"] == pytest.approx(1 / 3, abs=0.01)
        # Hai lý do "cách N m" khác số phải gộp thành một dòng, nếu không bảng
        # tổng kết sẽ có hàng trăm dòng chỉ khác nhau ở con số.
        assert len(report["rejectReasons"]) == 1

    def test_tap_rong_khong_chia_cho_0(self):
        assert summarize([])["acceptRate"] == 0.0
