"""Kiểm thử chỉ đường OSRM (lộ trình B16).

Không bài nào gọi OSRM thật: ``_fetch_route`` bị thay bằng hàm giả. Ba rủi ro
được canh riêng vì cả ba đều im lặng:

- Đảo thứ tự (kinh độ, vĩ độ) khi dựng URL — OSRM vẫn trả 200, tuyến nằm giữa biển.
- Khóa cache không làm tròn — mỗi lần GPS nhiễu vài mét là một lần tính tuyến mới.
- Câu hướng dẫn rơi về chuỗi tiếng Anh giữa giao diện tiếng Việt.
"""

from __future__ import annotations

import json

import pytest

from app import directions
from app.config import settings

# Phản hồi rút gọn nhưng giữ đúng hình dạng OSRM trả về, lấy từ một lần gọi
# THẬT tới OSRM tự dựng cho tuyến Quận 1 (xem docs/thesis).
OSRM_OK = {
    "code": "Ok",
    "routes": [
        {
            "distance": 465.2,
            "duration": 68.6,
            "geometry": {
                "type": "LineString",
                "coordinates": [[106.7009, 10.7757], [106.7018, 10.7784]],
            },
            "legs": [
                {
                    "steps": [
                        {
                            "distance": 19.8,
                            "duration": 4.1,
                            "name": "Đồng Khởi",
                            "maneuver": {"type": "depart", "modifier": "straight"},
                        },
                        {
                            "distance": 34.3,
                            "duration": 6.0,
                            "name": "Lê Thánh Tôn",
                            "maneuver": {"type": "turn", "modifier": "left"},
                        },
                        {
                            "distance": 2.0,
                            "duration": 0.4,
                            "name": "",
                            "maneuver": {"type": "turn", "modifier": "right"},
                        },
                        {
                            "distance": 138.1,
                            "duration": 25.0,
                            "name": "Pasteur",
                            "maneuver": {"type": "turn", "modifier": "right"},
                        },
                        {
                            "distance": 0.0,
                            "duration": 0.0,
                            "name": "",
                            "maneuver": {"type": "arrive", "modifier": "right"},
                        },
                    ]
                }
            ],
        }
    ],
}


class FakeRedis:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.setex_calls: list = []

    def get(self, key: str) -> str | None:
        return self.value

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))


# --- Khóa cache ---------------------------------------------------------------


def test_khoa_cache_lam_tron_toa_do() -> None:
    """Không làm tròn thì GPS nhiễu vài mét là một lần tính tuyến mới, và cache
    không bao giờ trúng."""
    a = directions._cache_key(10.77570001, 106.70090001, 10.7784, 106.7018, "car")
    b = directions._cache_key(10.77570009, 106.70090009, 10.7784, 106.7018, "car")
    assert a == b


def test_khoa_cache_phan_biet_hai_diem_cach_xa() -> None:
    a = directions._cache_key(10.7757, 106.7009, 10.7784, 106.7018, "car")
    b = directions._cache_key(10.8700, 106.7800, 10.7784, 106.7018, "car")
    assert a != b


def test_khoa_cache_phan_biet_theo_ho_so_di_chuyen() -> None:
    a = directions._cache_key(10.7757, 106.7009, 10.7784, 106.7018, "car")
    b = directions._cache_key(10.7757, 106.7009, 10.7784, 106.7018, "foot")
    assert a != b


def test_khoa_cache_khong_doi_xung() -> None:
    """Đi A→B và B→A có thể khác nhau (đường một chiều), nên khóa phải khác."""
    a = directions._cache_key(10.7757, 106.7009, 10.7784, 106.7018, "car")
    b = directions._cache_key(10.7784, 106.7018, 10.7757, 106.7009, "car")
    assert a != b


# --- Thứ tự toạ độ trong URL --------------------------------------------------


def test_url_dat_kinh_do_truoc_vi_do(monkeypatch) -> None:
    """OSRM nhận lon,lat. Đảo thứ tự không gây lỗi HTTP — chỉ cho ra một tuyến
    đường ở giữa biển. Đúng kiểu hỏng im lặng, nên phải có bài canh."""
    captured: dict = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(OSRM_OK).encode()

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(settings, "osrm_url", "http://osrm:5000", raising=False)
    monkeypatch.setattr(directions.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(directions.json, "load", lambda f: json.loads(f.read()))

    directions._fetch_route(10.7757, 106.7009, 10.7784, 106.7018, "car")

    # Kinh độ TP.HCM ~106, vĩ độ ~10 — cặp đầu tiên phải là 106.x,10.x
    assert "106.700900,10.775700;106.701800,10.778400" in captured["url"]


def test_khong_goi_gi_khi_chua_cau_hinh_osrm(monkeypatch) -> None:
    monkeypatch.setattr(settings, "osrm_url", "", raising=False)
    assert directions._fetch_route(10.7757, 106.7009, 10.7784, 106.7018, "car") is None


def _bat_url(monkeypatch) -> dict:
    """Chặn urlopen, trả về dict sẽ chứa URL mà _fetch_route thực sự gọi."""
    captured: dict = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(OSRM_OK).encode()

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(directions.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(directions.json, "load", lambda f: json.loads(f.read()))
    return captured


def test_xe_may_dung_do_thi_rieng_va_khong_con_xap_xi(monkeypatch) -> None:
    """Có đồ thị xe máy thật (osrm/motorbike.lua) thì phải gọi ĐÚNG container
    đó, và response không được tự hạ mình xuống `approximate`."""
    captured = _bat_url(monkeypatch)
    monkeypatch.setattr(settings, "osrm_url", "http://osrm:5000", raising=False)
    monkeypatch.setattr(
        settings, "osrm_motorbike_url", "http://osrm-motorbike:5000", raising=False
    )

    ket_qua = directions._fetch_route(10.7757, 106.7009, 10.7784, 106.7018, "motorbike")

    assert captured["url"].startswith("http://osrm-motorbike:5000/")
    assert ket_qua is not None
    assert ket_qua["approximate"] is False


def test_chua_dung_do_thi_xe_may_thi_lui_ve_o_to_va_danh_dau_xap_xi(monkeypatch) -> None:
    """Máy chưa chạy scripts/build_osrm_motorbike.sh KHÔNG được mất nút chỉ
    đường xe máy — thêm tính năng mà làm mất tính năng cũ là một bước lùi.

    Nhưng tuyến lúc đó tính bằng đồ thị Ô TÔ, nên BẮT BUỘC khai
    `approximate: true` dù hồ sơ "motorbike" khai approximate=False: đây đúng
    là chỗ dễ hỏng im lặng nhất của nhánh fallback.
    """
    captured = _bat_url(monkeypatch)
    monkeypatch.setattr(settings, "osrm_url", "http://osrm:5000", raising=False)
    monkeypatch.setattr(settings, "osrm_motorbike_url", "", raising=False)

    ket_qua = directions._fetch_route(10.7757, 106.7009, 10.7784, 106.7018, "motorbike")

    assert captured["url"].startswith("http://osrm:5000/")
    assert ket_qua is not None
    assert ket_qua["approximate"] is True


def test_khong_co_do_thi_nao_thi_tra_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "osrm_url", "", raising=False)
    monkeypatch.setattr(settings, "osrm_motorbike_url", "", raising=False)
    assert directions._fetch_route(10.7757, 106.7009, 10.7784, 106.7018, "motorbike") is None


# --- Định hình kết quả --------------------------------------------------------


def test_doc_dung_khoang_cach_va_thoi_gian() -> None:
    shaped = directions._shape_response(OSRM_OK, "car", False)
    assert shaped["distanceMeters"] == 465.2
    assert shaped["durationSeconds"] == 68.6
    assert shaped["durationMinutes"] == 1
    assert shaped["geometry"]["type"] == "LineString"
    assert shaped["mode"] == "car"
    assert shaped["approximate"] is False


def test_approximate_duoc_giu_nguyen_cho_motorbike() -> None:
    """Khi rơi vào nhánh fallback (chưa dựng đồ thị xe máy, xem
    `test_chua_dung_do_thi_xe_may_thi_lui_ve_o_to_va_danh_dau_xap_xi`), tuyến
    tính bằng đồ thị "car" nên response PHẢI tự khai báo approximate=True —
    không được để tầng gọi lầm tưởng đây là tuyến xe máy thật."""
    shaped = directions._shape_response(OSRM_OK, "motorbike", True)
    assert shaped["mode"] == "motorbike"
    assert shaped["approximate"] is True


def test_thoi_gian_lam_tron_len_toi_thieu_mot_phut() -> None:
    """0 phút trên giao diện đọc như 'đã tới nơi'."""
    payload = json.loads(json.dumps(OSRM_OK))
    payload["routes"][0]["duration"] = 12.0
    assert directions._shape_response(payload, "car", False)["durationMinutes"] == 1


def test_bo_cac_buoc_re_vun() -> None:
    """OSRM trả cả bước dài 2 m ở giao lộ. Hiện hết thì danh sách dài gấp ba mà
    không thêm thông tin nào."""
    steps = directions._shape_response(OSRM_OK, "car", False)["steps"]
    assert all(
        s["distanceMeters"] >= directions.MIN_STEP_DISTANCE_METERS or s["text"] == "Tới nơi"
        for s in steps
    )


def test_giu_lai_buoc_dau_va_buoc_cuoi_du_ngan() -> None:
    """'Tới nơi' dài 0 m nhưng là bước quan trọng nhất của danh sách."""
    texts = [s["text"] for s in directions._shape_response(OSRM_OK, "car", False)["steps"]]
    assert texts[0].startswith("Bắt đầu đi")
    assert texts[-1] == "Tới nơi"


def test_khong_co_tuyen_thi_tra_none() -> None:
    assert directions._shape_response({"code": "Ok", "routes": []}, "car", False) is None
    assert directions._shape_response({}, "car", False) is None


def test_hinh_hoc_sai_kieu_thi_tra_none() -> None:
    """geometries=polyline thay vì geojson sẽ cho chuỗi đã mã hoá — vẽ lên bản
    đồ thành rác chứ không báo lỗi."""
    payload = json.loads(json.dumps(OSRM_OK))
    payload["routes"][0]["geometry"] = "yxe@_qhbB"
    assert directions._shape_response(payload, "car", False) is None


# --- Câu hướng dẫn tiếng Việt --------------------------------------------------


@pytest.mark.parametrize(
    "kind,modifier,road,mong_doi",
    [
        ("depart", "straight", "Đồng Khởi", "Bắt đầu đi vào Đồng Khởi"),
        ("turn", "left", "Lê Thánh Tôn", "Rẽ trái vào Lê Thánh Tôn"),
        ("turn", "right", "Pasteur", "Rẽ phải vào Pasteur"),
        ("turn", "slight left", "Nam Kỳ Khởi Nghĩa", "Chếch sang trái vào Nam Kỳ Khởi Nghĩa"),
        ("turn", "uturn", "Hai Bà Trưng", "Quay đầu vào Hai Bà Trưng"),
        ("arrive", "right", "Lý Tự Trọng", "Tới nơi"),
        ("continue", "straight", "Nguyễn Huệ", "Đi tiếp vào Nguyễn Huệ"),
    ],
)
def test_cau_huong_dan_bang_tieng_viet(kind, modifier, road, mong_doi) -> None:
    step = {"name": road, "maneuver": {"type": kind, "modifier": modifier}}
    assert directions._maneuver_text(step) == mong_doi


def test_loai_re_la_khong_ro_ra_tieng_anh() -> None:
    """Một chuỗi tiếng Anh lọt vào giữa giao diện tiếng Việt trông như lỗi."""
    step = {"name": "Đường X", "maneuver": {"type": "exit rotary", "modifier": "khong-biet"}}
    text = directions._maneuver_text(step)
    assert text == "Đi tiếp vào Đường X"


def test_vong_xoay_neu_ro_loi_ra() -> None:
    step = {"name": "Nguyễn Văn Cừ", "maneuver": {"type": "roundabout", "exit": 2}}
    assert "vòng xoay" in directions._maneuver_text(step)
    assert "lối 2" in directions._maneuver_text(step)


# --- Cache --------------------------------------------------------------------


def test_cache_trung_thi_khong_goi_osrm(monkeypatch) -> None:
    def khong_duoc_goi(*args, **kwargs):  # pragma: no cover
        raise AssertionError("cache trúng mà vẫn gọi OSRM")

    monkeypatch.setattr(directions, "_fetch_route", khong_duoc_goi)
    shaped = directions._shape_response(OSRM_OK, "car", False)
    client = FakeRedis(json.dumps(shaped, ensure_ascii=False))

    result = directions.route(10.7757, 106.7009, 10.7784, 106.7018, client=client)

    assert result["cached"] is True
    assert result["distanceMeters"] == 465.2


def test_goi_xong_thi_ghi_cache_va_danh_dau_khong_phai_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        directions, "_fetch_route", lambda *a, **k: directions._shape_response(OSRM_OK, "car", False)
    )
    client = FakeRedis(None)

    result = directions.route(10.7757, 106.7009, 10.7784, 106.7018, client=client)

    assert result["cached"] is False
    assert len(client.setex_calls) == 1
    assert client.setex_calls[0][1] == directions.CACHE_TTL_SECONDS


def test_cache_giu_nguyen_tieng_viet_co_dau(monkeypatch) -> None:
    """ensure_ascii=False. Escape thì tên đường đọc ra dạng \\u1ec7."""
    monkeypatch.setattr(
        directions, "_fetch_route", lambda *a, **k: directions._shape_response(OSRM_OK, "car", False)
    )
    client = FakeRedis(None)

    directions.route(10.7757, 106.7009, 10.7784, 106.7018, client=client)

    assert "Lê Thánh Tôn" in client.setex_calls[0][2]


def test_osrm_chet_thi_tra_none_chu_khong_no(monkeypatch) -> None:
    monkeypatch.setattr(directions, "_fetch_route", lambda *a, **k: None)
    assert directions.route(10.7757, 106.7009, 10.7784, 106.7018, client=FakeRedis(None)) is None
