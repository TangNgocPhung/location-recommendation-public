"""Kiểm thử phần thuần logic của trang chi tiết địa điểm.

Phần chạm Postgres được kiểm ở tầng integration; ở đây chỉ những chỗ sai được
mà KHÔNG có lỗi nào nổi lên — và cả ba đều là sai về sự thật chứ không phải sai
về kỹ thuật:

- Lịch mở cửa lệch một ngày (``datetime.weekday()`` là 0=thứ Hai, còn
  ``strftime('%w')`` là 0=Chủ Nhật). Bảng vẫn đủ bảy dòng, vẫn đẹp, chỉ là sai.
- Gộp ``unknown`` với ``closed``: 73 POI ghi giờ theo cú pháp OSM chưa đọc được
  sẽ hiện ra như những quán đóng cửa cả tuần.
- ``average = 0.0`` khi chưa có đánh giá nào. "Chưa ai chấm" và "bị chấm 0 điểm"
  là hai câu hoàn toàn khác nhau, và cái sau là vu khống một hàng quán có thật.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import poi_detail
from app.opening_hours import opening_status, parse_opening_hours

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")

# Tuần 7-13/09/2026: thứ Hai rơi vào ngày 7.
THU_HAI = datetime(2026, 9, 7, 9, 0, tzinfo=SAIGON)
CHU_NHAT = datetime(2026, 9, 13, 9, 0, tzinfo=SAIGON)


def lich(raw: str | None) -> dict:
    """Đi qua đúng đường mà `fetch_detail` đi: parse rồi mới chuẩn hoá."""
    return poi_detail.normalize_opening_hours(parse_opening_hours(raw))


# --- normalize_opening_hours: bốn khoá của hợp đồng ---------------------------


@pytest.mark.parametrize(
    "raw", [None, "", "24/7", "Mo-Fr 07:00-22:00", "sunrise-sunset", "Mo-Fr 07:00+"]
)
def test_luon_bo_du_bon_khoa_cua_hop_dong(raw) -> None:
    """`parse_opening_hours` bỏ `parseStatus` ở nhánh "24/7" và trả hẳn `{}` khi
    POI không có giờ. Giao diện thì cần bốn khoá luôn có mặt để khỏi phải đoán."""
    ket_qua = lich(raw)
    assert set(ket_qua) == {"raw", "parseStatus", "periods", "alwaysOpen"}
    assert isinstance(ket_qua["periods"], list)
    assert isinstance(ket_qua["alwaysOpen"], bool)


def test_khong_co_the_opening_hours_thi_ghi_missing() -> None:
    """"Không khai giờ" khác "khai một chuỗi không đọc được". Gộp lại thì mất
    luôn khả năng đo xem parser còn thiếu bao nhiêu phần trăm dữ liệu."""
    assert lich(None)["parseStatus"] == poi_detail.PARSE_STATUS_MISSING
    assert lich(None)["raw"] is None


def test_24_7_duoc_ghi_la_parsed_chu_khong_phai_mot_nhan_rieng() -> None:
    """Chuỗi "24/7" ĐÃ đọc hiểu được, nên `weekHours` dựng từ nó là đáng tin —
    khác hẳn `unsupported`, nơi ta thật sự không biết gì."""
    ket_qua = lich("24/7")
    assert ket_qua["alwaysOpen"] is True
    assert ket_qua["parseStatus"] == "parsed"


def test_chuoi_khong_doc_duoc_van_giu_nguyen_van_de_hien_ra() -> None:
    """Giao diện hiện lại nguyên văn chuỗi OSM thay vì một bảng bịa ra."""
    ket_qua = lich("Mo-Fr sunrise-sunset")
    assert ket_qua["parseStatus"] == "unsupported"
    assert ket_qua["raw"] == "Mo-Fr sunrise-sunset"


def test_normalize_chiu_duoc_dau_vao_khong_phai_dict() -> None:
    for dau_vao in (None, {}, "24/7", [], 0):
        assert poi_detail.normalize_opening_hours(dau_vao)["parseStatus"] is not None


# --- weekHours: bảy ngày, thứ Hai trước --------------------------------------


@pytest.mark.parametrize(
    "raw",
    [None, "24/7", "Mo-Fr 07:00-22:00", "sunrise-sunset", "Mo 06:00-10:00; Mo 17:00-21:00"],
)
def test_luon_dung_bay_phan_tu(raw) -> None:
    ngay = poi_detail.week_hours(lich(raw), "Asia/Ho_Chi_Minh", THU_HAI)
    assert len(ngay) == 7


@pytest.mark.parametrize("raw", [None, "24/7", "Mo-Fr 07:00-22:00", "sunrise-sunset"])
def test_bat_dau_tu_thu_hai_va_dung_thu_tu(raw) -> None:
    """0 = thứ Hai theo `datetime.weekday()`, trùng quy ước của `DAY_INDEX` bên
    `opening_hours`. Lấy nhầm gốc Chủ Nhật thì cả bảng lệch đúng một ngày — sai
    số im lặng nhất của cả trang."""
    ngay = poi_detail.week_hours(lich(raw), "Asia/Ho_Chi_Minh", THU_HAI)

    assert [d["weekday"] for d in ngay] == list(range(7))
    assert [d["label"] for d in ngay] == [
        "Thứ Hai",
        "Thứ Ba",
        "Thứ Tư",
        "Thứ Năm",
        "Thứ Sáu",
        "Thứ Bảy",
        "Chủ Nhật",
    ]


@pytest.mark.parametrize(
    "ngay_trong_thang,weekday_mong_doi",
    [(7, 0), (8, 1), (9, 2), (10, 3), (11, 4), (12, 5), (13, 6)],
)
def test_dung_mot_ngay_duoc_danh_dau_hom_nay(ngay_trong_thang, weekday_mong_doi) -> None:
    at = datetime(2026, 9, ngay_trong_thang, 14, 30, tzinfo=SAIGON)
    ngay = poi_detail.week_hours(lich("Mo-Su 07:00-22:00"), "Asia/Ho_Chi_Minh", at)

    hom_nay = [d for d in ngay if d["isToday"]]
    assert len(hom_nay) == 1
    assert hom_nay[0]["weekday"] == weekday_mong_doi


def test_hom_nay_tinh_theo_mui_gio_dia_diem_khong_theo_utc() -> None:
    """22:00 UTC Chủ Nhật đã là 05:00 thứ Hai ở TP.HCM. Lấy theo UTC thì mỗi
    đêm, bảng giờ tô sáng nhầm ngày suốt bảy tiếng."""
    at = datetime(2026, 9, 13, 22, 0, tzinfo=ZoneInfo("UTC"))
    ngay = poi_detail.week_hours(lich("Mo-Su 07:00-22:00"), "Asia/Ho_Chi_Minh", at)

    assert [d["weekday"] for d in ngay if d["isToday"]] == [0]


def test_mui_gio_khong_ton_tai_thi_van_du_bay_ngay() -> None:
    """Một giá trị `timezone` rác trong DB không được phép làm 500 cả trang."""
    ngay = poi_detail.week_hours(lich("Mo-Fr 07:00-22:00"), "Sao/Hoa", THU_HAI)
    assert len(ngay) == 7
    assert sum(1 for d in ngay if d["isToday"]) == 1


# --- weekHours: unknown KHÁC closed ------------------------------------------


@pytest.mark.parametrize("raw", ["sunrise-sunset", "Mo-Fr 07:00+", "Mo-Fr 07:00-22:00 open"])
def test_chua_doc_duoc_gio_thi_ca_bay_ngay_unknown_va_khong_doan_bua(raw) -> None:
    """Đoán ra một lịch mở cửa từ một chuỗi không đọc được là loại sai số tệ
    nhất: nó trông hợp lý."""
    ngay = poi_detail.week_hours(lich(raw), "Asia/Ho_Chi_Minh", THU_HAI)

    assert all(d["unknown"] is True for d in ngay)
    assert all(d["intervals"] == [] for d in ngay)
    # Và TUYỆT ĐỐI không được gộp sang `closed`: gộp thì 73 POI ghi giờ theo cú
    # pháp OSM lạ hiện ra như những quán đóng cửa cả tuần.
    assert all(d["closed"] is False for d in ngay)


def test_khong_co_the_gio_thi_cung_khong_doan() -> None:
    ngay = poi_detail.week_hours(lich(None), "Asia/Ho_Chi_Minh", THU_HAI)

    assert all(d["unknown"] is True for d in ngay)
    assert all(d["closed"] is False for d in ngay)


def test_ngay_dong_cua_that_thi_closed_chu_khong_phai_unknown() -> None:
    """Ngược lại của bài trên: ở đây ta ĐỌC ĐƯỢC giờ mở và biết chắc Chủ Nhật
    quán nghỉ. Gắn `unknown` cho nó là vứt đi một thông tin có thật."""
    ngay = poi_detail.week_hours(
        lich("Mo-Fr 07:00-22:00; Sa 08:00-12:00"), "Asia/Ho_Chi_Minh", THU_HAI
    )

    chu_nhat = ngay[6]
    assert chu_nhat["closed"] is True
    assert chu_nhat["unknown"] is False
    assert chu_nhat["intervals"] == []

    assert [d["intervals"] for d in ngay[:5]] == [[{"opens": "07:00", "closes": "22:00"}]] * 5
    assert ngay[5]["intervals"] == [{"opens": "08:00", "closes": "12:00"}]
    assert all(d["unknown"] is False for d in ngay)


def test_mo_24_7_thi_khong_hien_00_00_den_00_00() -> None:
    """"00:00 – 00:00" đọc lên giống hệt "đóng cửa"."""
    ngay = poi_detail.week_hours(lich("24/7"), "Asia/Ho_Chi_Minh", THU_HAI)

    assert all(d["intervals"] == [{"opens": "00:00", "closes": "24:00"}] for d in ngay)
    assert all(d["unknown"] is False and d["closed"] is False for d in ngay)


def test_nhieu_khoang_trong_mot_ngay_sap_theo_gio_mo() -> None:
    """Quán nghỉ trưa. Không sắp thì bảng hiện "17:00-21:00" trước "06:00-10:00"
    và người đọc tưởng mình đọc nhầm."""
    ngay = poi_detail.week_hours(
        lich("Mo 17:00-21:00; Mo 06:00-10:00"), "Asia/Ho_Chi_Minh", THU_HAI
    )

    assert ngay[0]["intervals"] == [
        {"opens": "06:00", "closes": "10:00"},
        {"opens": "17:00", "closes": "21:00"},
    ]
    assert ngay[1]["closed"] is True


# --- weekHours: ca qua nửa đêm phải khớp với badge "Đang mở" ------------------


def test_ca_qua_nua_dem_tran_sang_ngay_hom_sau() -> None:
    """`openingStatus` và `weekHours` đi chung một phản hồi nên không được nói
    ngược nhau. 'Mo-Sa 08:00-06:00' lúc 03:00 Chủ Nhật: badge ghi "Đang mở, còn
    180 phút" trong khi bảng tuần ghi Chủ Nhật "Đóng cửa" — POI 'Miquafood'
    trong DB dính đúng ca này."""
    at = datetime(2026, 9, 13, 3, 0, tzinfo=SAIGON)
    raw = "Mo-Sa 08:00-06:00"

    trang_thai = opening_status(parse_opening_hours(raw), "Asia/Ho_Chi_Minh", at)
    ngay = poi_detail.week_hours(lich(raw), "Asia/Ho_Chi_Minh", at)
    chu_nhat = ngay[6]

    assert trang_thai["openNow"] is True
    assert chu_nhat["isToday"] is True
    assert chu_nhat["closed"] is False
    assert chu_nhat["intervals"] == [{"opens": "00:00", "closes": "06:00"}]
    # Ngày bắt đầu vẫn giữ nguyên ca gốc, và thứ Ba nhận CẢ hai phần.
    assert ngay[0]["intervals"] == [{"opens": "08:00", "closes": "06:00"}]
    assert ngay[1]["intervals"] == [
        {"opens": "00:00", "closes": "06:00"},
        {"opens": "08:00", "closes": "06:00"},
    ]


def test_dong_cua_luc_nua_dem_thi_khong_tran_sang_hom_sau() -> None:
    """Cái bẫy của bản vá trên: parser quy "24:00" thành "00:00", nên
    'Mo-Su 11:00-24:00' trông y hệt một ca qua đêm. Trải nó ra sẽ đẻ thêm khoảng
    "00:00 – 00:00" dài 0 phút và biến buổi sáng đóng cửa thành buổi sáng mở."""
    ngay = poi_detail.week_hours(lich("Mo-Su 11:00-24:00"), "Asia/Ho_Chi_Minh", THU_HAI)

    assert all(d["intervals"] == [{"opens": "11:00", "closes": "00:00"}] for d in ngay)


def test_ngay_dong_cua_khong_bi_ca_qua_dem_cua_hom_truoc_mo_nham() -> None:
    """Ca của hôm trước chỉ tràn sang khi nó THẬT SỰ vắt qua nửa đêm. Quán chỉ
    mở thứ Hai hai ca trong ngày thì thứ Ba vẫn phải là đóng cửa."""
    ngay = poi_detail.week_hours(
        lich("Mo 06:00-10:00; Mo 17:00-21:00"), "Asia/Ho_Chi_Minh", THU_HAI
    )

    assert ngay[1]["closed"] is True
    assert ngay[1]["intervals"] == []


def test_tinh_gio_tuan_khong_duoc_sua_vao_lich_goc() -> None:
    """`week_hours` chạy bảy vòng trên cùng một `schedule`; lỡ tay ghi vào
    `periods` thì vòng sau đọc phải dữ liệu đã hỏng mà không ai thấy."""
    schedule = lich("Mo-Sa 08:00-06:00")
    truoc = deepcopy(schedule)

    poi_detail.week_hours(schedule, "Asia/Ho_Chi_Minh", THU_HAI)

    assert schedule == truoc


def test_moi_phan_tu_du_dung_sau_khoa_cua_hop_dong() -> None:
    for d in poi_detail.week_hours(lich("24/7"), "Asia/Ho_Chi_Minh", THU_HAI):
        assert set(d) == {"weekday", "label", "isToday", "closed", "unknown", "intervals"}
        for khoang in d["intervals"]:
            assert set(khoang) == {"opens", "closes"}


# --- reviewSummary: NULL khác 0 ----------------------------------------------


class FakeCursor:
    """Trả lần lượt: kết quả histogram, rồi kết quả danh sách đánh giá.

    `_review_block` chạy đúng hai câu lệnh theo thứ tự đó; giữ nguyên thứ tự để
    bài test hỏng nếu ai đó đảo hai truy vấn (sẽ đọc nhầm cột).
    """

    def __init__(self, histogram_rows: list[dict], review_rows: list[dict]) -> None:
        self._batches = [list(histogram_rows), list(review_rows)]
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.executed.append((sql, params or {}))

    def fetchall(self) -> list[dict]:
        return self._batches.pop(0)


def review_row(**ghi_de) -> dict:
    row = {
        "id": "aaaaaaaa-0000-4000-8000-000000000001",
        "authorName": "Lan",
        "rating": 5,
        "title": "Ngon",
        "body": "Cà phê đậm.",
        "language": "vi",
        "source": "user",
        "helpfulCount": 3,
        "created_at": datetime(2026, 9, 1, 8, 30, tzinfo=SAIGON),
    }
    row.update(ghi_de)
    return row


def test_chua_co_danh_gia_nao_thi_average_la_none_chu_khong_phai_0() -> None:
    """"Chưa ai chấm" KHÁC "bị chấm 0 điểm". Bảng `poi_reviews` hiện đang rỗng,
    nên đây là câu trả lời cho gần như mọi POI trong database — hỏng ở đây là
    hỏng trên toàn bộ dữ liệu."""
    tom_tat, danh_sach = poi_detail._review_block(FakeCursor([], []), "poi-1")

    assert tom_tat["count"] == 0
    assert tom_tat["average"] is None
    assert tom_tat["average"] != 0
    assert danh_sach == []


def test_histogram_du_nam_khoa_ke_ca_khi_dem_bang_0() -> None:
    """Thiếu khoá thì biểu đồ cột phía React mất hẳn một cột, và người xem đọc
    thành "không ai chấm 2 sao" thay vì "0 người chấm 2 sao"."""
    tom_tat, _ = poi_detail._review_block(FakeCursor([], []), "poi-1")

    assert tom_tat["histogram"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}


def test_histogram_van_du_nam_khoa_khi_chi_co_vai_muc_sao() -> None:
    tom_tat, _ = poi_detail._review_block(
        FakeCursor([{"stars": 5, "total": 3}, {"stars": 4, "total": 1}], []), "poi-1"
    )

    assert tom_tat["histogram"] == {"1": 0, "2": 0, "3": 0, "4": 1, "5": 3}
    assert tom_tat["count"] == 4


def test_trung_binh_tinh_dung_va_khop_voi_bieu_do_nguoi_dung_nhin_thay() -> None:
    """Tính trung bình từ một truy vấn AVG() riêng thì con số có thể lệch so với
    chính biểu đồ nằm ngay bên cạnh nó."""
    histogram_rows = [
        {"stars": 5, "total": 2},
        {"stars": 4, "total": 1},
        {"stars": 1, "total": 1},
    ]
    tom_tat, _ = poi_detail._review_block(FakeCursor(histogram_rows, []), "poi-1")

    assert tom_tat["count"] == 4
    assert tom_tat["average"] == 3.75
    tong = sum(int(sao) * so for sao, so in tom_tat["histogram"].items())
    assert round(tong / tom_tat["count"], 2) == tom_tat["average"]


def test_danh_sach_danh_gia_doi_thoi_gian_sang_chuoi_iso() -> None:
    """`datetime` thô không đi qua được `JSONResponse` mặc định của FastAPI."""
    _, danh_sach = poi_detail._review_block(
        FakeCursor([{"stars": 5, "total": 1}], [review_row()]), "poi-1"
    )

    assert len(danh_sach) == 1
    assert set(danh_sach[0]) == {
        "id",
        "authorName",
        "rating",
        "title",
        "body",
        "language",
        "source",
        "helpfulCount",
        "createdAt",
    }
    assert isinstance(danh_sach[0]["createdAt"], str)
    assert danh_sach[0]["createdAt"].startswith("2026-09-01T08:30")


def test_khong_ro_ten_nguoi_viet_thi_de_none() -> None:
    """Đánh giá ẩn danh có thật. Bịa ra "Người dùng ẩn danh" ở tầng dữ liệu thì
    giao diện không phân biệt được nữa."""
    _, danh_sach = poi_detail._review_block(
        FakeCursor([{"stars": 3, "total": 1}], [review_row(authorName=None, title=None)]),
        "poi-1",
    )

    assert danh_sach[0]["authorName"] is None
    assert danh_sach[0]["title"] is None


def test_danh_sach_danh_gia_bi_gioi_han_va_lay_moi_nhat_truoc() -> None:
    cursor = FakeCursor([], [])
    poi_detail._review_block(cursor, "poi-1")

    sql_danh_sach, params = cursor.executed[1]
    assert params["limit"] == poi_detail.REVIEW_LIMIT
    assert "ORDER BY created_at DESC" in sql_danh_sach


# --- distance_label -----------------------------------------------------------


@pytest.mark.parametrize(
    "met,mong_doi",
    [
        (None, None),
        (0, "cách 0 m"),
        (320, "cách 320 m"),
        (317.4, "cách 320 m"),
        (999, "cách 1000 m"),
        (1000, "cách 1,0 km"),
        (1400, "cách 1,4 km"),
        (12_345, "cách 12,3 km"),
    ],
)
def test_nhan_khoang_cach(met, mong_doi) -> None:
    assert poi_detail.distance_label(met) == mong_doi


def test_nhan_khoang_cach_dung_dau_phay_thap_phan_kieu_viet() -> None:
    """"1.4 km" giữa một giao diện tiếng Việt đọc như một nghìn tư ki-lô-mét."""
    nhan = poi_detail.distance_label(1400)
    assert "," in nhan and "." not in nhan


def test_khong_co_vi_tri_nguoi_dung_thi_khong_co_khoang_cach() -> None:
    """Điền một con số vào đây khi chưa biết người dùng đứng ở đâu là bịa."""
    assert poi_detail.distance_label(None) is None


# --- Chốt UUID của endpoint chi tiết ------------------------------------------


@pytest.mark.parametrize(
    "poi_id,hop_le",
    [
        ("9e257303-3642-4064-95f9-4048e6a2287e", True),
        ("9E257303-3642-4064-95F9-4048E6A2287E", True),
        # Năm dạng Python `UUID()` nuốt được mà kiểu `uuid` của Postgres từ chối.
        ("urn:uuid:9e257303-3642-4064-95f9-4048e6a2287e", False),
        ("uuid:9e257303-3642-4064-95f9-4048e6a2287e", False),
        ("}9e257303-3642-4064-95f9-4048e6a2287e", False),
        ("{9e257303-3642-4064-95f9-4048e6a2287e", False),
        ("9-e2573033642406495f94048e6a2287e", False),
        ("abc", False),
        ("", False),
    ],
)
def test_chot_uuid_chan_dung_thu_postgres_se_tu_choi(poi_id, hop_le) -> None:
    """Chuỗi lọt chốt sẽ đi thẳng vào `WHERE id = %(poi_id)s` của
    `fetch_detail`; psycopg ném DataError giữa chừng và người dùng nhận 500
    "Internal Server Error" kèm traceback DB trong log, thay vì 400 tiếng Việt.

    Import nằm trong hàm vì `app.api` dựng cả FastAPI app khi nạp module — cả
    bộ test đơn vị còn lại không cần tới nó."""
    from app.api import is_postgres_uuid

    assert is_postgres_uuid(poi_id) is hop_le
