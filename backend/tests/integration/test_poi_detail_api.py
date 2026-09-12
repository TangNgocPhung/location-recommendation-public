"""Hợp đồng của trang chi tiết địa điểm, kiểm trên stack thật.

Tầng unit đã canh phần thuần logic (giờ mở, đánh giá, khoảng cách). Ở đây chỉ
kiểm những thứ CHỈ hỏng khi chạy thật:

- Thiếu hẳn một trường trong payload. Giao diện đọc `undefined` rồi hiện ra ô
  trống — không lỗi, không cảnh báo, chỉ là một mục thông tin biến mất.
- Cột đã có trong code nhưng chưa có trong database. `pois.website`/`pois.phone`
  do migration 0012 thêm vào; chưa `alembic upgrade head` thì endpoint này 500.
- `distanceMeters` trả 0 thay vì null khi không truyền `lat/lng`. 0 mét đọc
  thành "bạn đang đứng ngay đó".
- `{poi_id}` nuốt mất một route khác. `/api/v1/pois/{id}` và `/api/pois/nearby`
  khác tiền tố nên không đụng nhau, nhưng đó là thứ phải kiểm chứ không phải
  thứ để tin.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 and start the Docker Compose stack",
    ),
]

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
SESSION_ID = "22222222-2222-4222-8222-222222222222"

# Toàn bộ khoá bắt buộc của hợp đồng A. Liệt kê tường minh chứ không kiểm
# "có ít nhất N khoá": quên một trường là một mục thông tin biến mất khỏi giao
# diện mà không ai thấy.
KHOA_BAT_BUOC = {
    "id",
    "name",
    "description",
    "category",
    "categoryLabel",
    "address",
    "district",
    "city",
    "countryCode",
    "latitude",
    "longitude",
    "brand",
    "website",
    "phone",
    "rating",
    "reviewCount",
    "ratingSource",
    "popularityScore",
    "priceLevel",
    "tags",
    "amenities",
    "sponsored",
    "timezone",
    "openingHours",
    "openingStatus",
    "weekHours",
    "distanceMeters",
    "etaMinutes",
    "popularityWindows",
    "reviewSummary",
    "reviews",
    "similar",
    "provenance",
}

# Trùng `poi_detail.SIMILAR_RADIUS_METERS`. Ghi lại ở đây thay vì import: bài
# test integration kiểm HỢP ĐỒNG mà giao diện nhìn thấy, nên nó phải hỏng khi ai
# đó đổi hằng số bên app mà quên rằng giao diện đang dựa vào con số này.
BAN_KINH_TUONG_TU_METERS = 2_000

NHAN_THU_TRONG_TUAN = [
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
]


def mot_poi_that() -> dict:
    """Lấy một POI có thật từ chính đường tìm kiếm mà người dùng đi qua.

    Không hardcode UUID: dữ liệu được nhập lại bằng `poi_id = gen_random_uuid()`
    nên mọi id ghi cứng đều chết sau lần nhập kế tiếp.
    """
    response = httpx.post(
        f"{API_BASE_URL}/api/v1/search",
        timeout=20,
        headers={"X-Session-ID": SESSION_ID},
        json={
            "query": "cà phê gần Bến Thành",
            "latitude": 10.7757,
            "longitude": 106.7009,
            "radius": 5000,
            "limit": 5,
        },
    )
    assert response.status_code == 200
    ket_qua = response.json()["results"]
    assert ket_qua, "không tìm được POI nào để kiểm trang chi tiết"
    return ket_qua[0]


@pytest.fixture(scope="module")
def poi() -> dict:
    return mot_poi_that()


def test_tra_du_moi_khoa_cua_hop_dong(poi) -> None:
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20)

    assert response.status_code == 200, response.text
    payload = response.json()
    thieu = KHOA_BAT_BUOC - set(payload)
    assert not thieu, f"thiếu khoá trong payload: {sorted(thieu)}"
    assert payload["id"] == poi["id"]
    assert payload["timezone"] == "Asia/Ho_Chi_Minh"


def test_gio_mo_luon_du_bay_ngay_bat_dau_tu_thu_hai(poi) -> None:
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()
    tuan = payload["weekHours"]

    assert len(tuan) == 7
    assert [d["weekday"] for d in tuan] == list(range(7))
    assert [d["label"] for d in tuan] == NHAN_THU_TRONG_TUAN
    assert sum(1 for d in tuan if d["isToday"]) == 1
    # unknown và closed không bao giờ được đúng cùng lúc: "không biết giờ mở" và
    # "hôm nay nghỉ" là hai câu khác nhau.
    assert all(not (d["unknown"] and d["closed"]) for d in tuan)


def test_opening_hours_du_bon_khoa_va_khop_voi_week_hours(poi) -> None:
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()
    gio = payload["openingHours"]

    assert set(gio) == {"raw", "parseStatus", "periods", "alwaysOpen"}
    # Bảng bảy ngày chỉ đáng tin khi chuỗi OSM đã đọc hiểu được. Hai chỗ này lệch
    # nhau nghĩa là giao diện hiện một lịch mở cửa được suy ra từ hư không.
    chua_doc_duoc = gio["parseStatus"] != "parsed"
    assert all(d["unknown"] is chua_doc_duoc for d in payload["weekHours"])
    if chua_doc_duoc:
        assert set(payload["openingStatus"].values()) == {None}


def test_khong_truyen_vi_tri_thi_khoang_cach_va_eta_la_null(poi) -> None:
    """0 mét đọc thành "bạn đang đứng ngay đó" — một con số bịa, không phải một
    giá trị mặc định vô hại."""
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()

    assert payload["distanceMeters"] is None
    assert payload["etaMinutes"] is None


def test_truyen_vi_tri_thi_co_khoang_cach_va_du_ba_phuong_tien(poi) -> None:
    payload = httpx.get(
        f"{API_BASE_URL}/api/v1/pois/{poi['id']}",
        params={"lat": 10.7757, "lng": 106.7009},
        timeout=20,
    ).json()

    assert isinstance(payload["distanceMeters"], (int, float))
    assert payload["distanceMeters"] >= 0
    assert set(payload["etaMinutes"]) == {"walk", "motorbike", "car"}
    # Đi bộ không bao giờ nhanh hơn xe máy trên cùng một quãng đường.
    assert payload["etaMinutes"]["walk"] >= payload["etaMinutes"]["motorbike"]


def test_danh_gia_chua_co_thi_la_null_chu_khong_phai_0(poi) -> None:
    """2.982/3.010 POI không có điểm nào. `rating: 0` biến "chưa ai chấm" thành
    "bị chấm 0 điểm" trên gần như toàn bộ database."""
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()

    assert payload["rating"] is None or payload["rating"] > 0
    if payload["rating"] is None:
        assert payload["ratingSource"] is None

    tom_tat = payload["reviewSummary"]
    assert set(tom_tat["histogram"]) == {"1", "2", "3", "4", "5"}
    # `reviews` bị giới hạn 10 dòng, `count` là tổng thật — nên count >= số dòng
    # trả về, và không bao giờ ngược lại.
    assert tom_tat["count"] >= len(payload["reviews"])
    if tom_tat["count"] == 0:
        assert tom_tat["average"] is None
    # `reviewSummary.count` (bảng poi_reviews, hiện rỗng) KHÁC `reviewCount`
    # (cột seed/bên thứ ba). Gộp hai con số khác nguồn thì không ai truy được gốc.
    assert "reviewCount" in payload and "count" in tom_tat


def test_provenance_noi_ro_du_lieu_tu_dau_ra(poi) -> None:
    """Hội đồng sẽ hỏi "con số này lấy ở đâu". Trang chi tiết phải tự trả lời
    được mà không cần mở database."""
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()
    provenance = payload["provenance"]

    assert set(provenance) == {"source", "sourceId", "updatedAt", "h3", "embeddingModel"}
    assert provenance["source"] in {"openstreetmap", "seed"}
    assert set(provenance["h3"]) == {"r7", "r8", "r9"}


def test_dia_diem_tuong_tu_co_do_khoang_cach_that(poi) -> None:
    payload = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}", timeout=20).json()

    for tuong_tu in payload["similar"]:
        assert set(tuong_tu) == {
            "id",
            "name",
            "categoryLabel",
            "address",
            "rating",
            "reviewCount",
            "distanceMeters",
            "latitude",
            "longitude",
            "reason",
        }
        assert tuong_tu["id"] != poi["id"]
        # ST_DWithin dùng bán kính 2 km; cộng 1 m cho sai số dấu phẩy động của
        # phép tính trên geography.
        assert tuong_tu["distanceMeters"] <= BAN_KINH_TUONG_TU_METERS + 1
        assert tuong_tu["rating"] is None or tuong_tu["rating"] > 0

    # Sắp theo khoảng cách tăng dần: "địa điểm tương tự" mà xa hơn đứng trước thì
    # danh sách trông như xếp ngẫu nhiên.
    khoang_cach = [t["distanceMeters"] for t in payload["similar"]]
    assert khoang_cach == sorted(khoang_cach)


def test_id_khong_phai_uuid_thi_400_voi_thong_bao_tieng_viet() -> None:
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/khong-phai-uuid", timeout=10)

    assert response.status_code == 400
    assert response.json() == {"detail": "poi_id phải là UUID"}


def test_uuid_khong_ton_tai_thi_404_chu_khong_phai_200_rong() -> None:
    """Trả 200 với payload rỗng thì giao diện hiện một trang chi tiết trống mà
    không ai biết là địa điểm không tồn tại."""
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/{uuid.uuid4()}", timeout=10)

    assert response.status_code == 404
    assert response.json() == {"detail": "Không có địa điểm này"}


def test_khong_nuot_mat_route_nearby() -> None:
    """`/api/v1/pois/{poi_id}` và `/api/pois/nearby` khác tiền tố nên không đụng
    nhau — nhưng đó là thứ phải kiểm, không phải thứ để tin."""
    response = httpx.get(
        f"{API_BASE_URL}/api/pois/nearby",
        params={"lat": 10.7757, "lng": 106.7009, "radius": 1000},
        timeout=20,
    )

    assert response.status_code == 200, response.text
    # Vẫn là danh sách POI quanh đây, không phải "poi_id phải là UUID".
    assert isinstance(response.json(), list)


# --- Ảnh (endpoint tách riêng, CÓ thể gọi Wikimedia nên chậm) -----------------


def test_anh_luon_ghi_ro_do_tin_cay(poi) -> None:
    """Nhãn `confidence` là toàn bộ lý do endpoint này tồn tại dưới dạng này.

    Một tấm ảnh con phố hiện lên không nhãn giữa trang chi tiết một quán cà phê
    là một lời nói dối im lặng — người xem mặc định đó là ảnh của quán.
    """
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/{poi['id']}/photos", timeout=60)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"poiId", "status", "fetchedAt", "photos"}
    # Ba trạng thái, không được rút còn hai: `empty` là "đã hỏi, không có ảnh",
    # `unavailable` là "chưa hỏi được".
    assert payload["status"] in {"ready", "empty", "unavailable"}
    if payload["status"] != "ready":
        assert payload["photos"] == []

    for anh in payload["photos"]:
        assert anh["confidence"] in {"place", "area"}
        assert anh["source"] == "wikimedia"
        assert anh["url"].startswith("https://")
        assert anh["sourceUrl"].startswith("https://commons.wikimedia.org/")
        if anh["confidence"] == "area":
            # Không có khoảng cách thì giao diện không ghi nổi "cách N m", và
            # tấm ảnh khu vực đó trở thành ảnh không nhãn.
            assert isinstance(anh["distanceMeters"], (int, float))
        else:
            # "cách 0 m" cho ảnh của chính địa điểm là bịa ra một phép đo.
            assert anh["distanceMeters"] is None


def test_anh_cua_id_khong_phai_uuid_thi_400() -> None:
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/khong-phai-uuid/photos", timeout=10)

    assert response.status_code == 400
    assert response.json() == {"detail": "poi_id phải là UUID"}


def test_anh_cua_uuid_khong_ton_tai_thi_404() -> None:
    response = httpx.get(f"{API_BASE_URL}/api/v1/pois/{uuid.uuid4()}/photos", timeout=10)

    assert response.status_code == 404
    assert response.json() == {"detail": "Không có địa điểm này"}
