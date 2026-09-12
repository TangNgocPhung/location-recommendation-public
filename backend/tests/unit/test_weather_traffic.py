"""Kiểm thử Weather & Traffic Density Injection (lộ trình B12).

Không bài nào chạm mạng thật: ``_fetch_remote`` bị thay bằng hàm giả. Một bộ
test mà xanh/đỏ phụ thuộc trời TP.HCM hôm đó có mưa hay không thì không phải
là test.

Ba rủi ro được canh riêng, vì cả ba đều im lặng:
- ``contextScore`` vượt khỏi thang [0,1] sau khi nhân hệ số thời tiết.
- Hạ điểm bệnh viện vì trời mưa.
- Nhân hệ số tắc đường vào thời gian ĐI BỘ.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import spatio_temporal, traffic, weather
from app.config import settings

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
HCM_LAT, HCM_LON = 10.7757, 106.7009

MUA_VUA = {"weatherCode": 63, "precipitationMm": 1.2, "isWet": True, "isHeavyRain": False}
MUA_TO = {"weatherCode": 95, "precipitationMm": 8.0, "isWet": True, "isHeavyRain": True}
TROI_KHO = {"weatherCode": 0, "precipitationMm": 0.0, "isWet": False, "isHeavyRain": False}


# --- Weather: phân loại -------------------------------------------------------


def test_classify_doc_dung_du_lieu_that_cua_open_meteo() -> None:
    """Payload lấy nguyên văn từ một lần gọi thật tới api.open-meteo.com cho
    toạ độ TP.HCM (mã 53 = mưa phùn vừa)."""
    ket_qua = weather.classify(
        {
            "time": "2026-09-11T18:00",
            "temperature_2m": 25.0,
            "precipitation": 0.2,
            "weather_code": 53,
            "wind_speed_10m": 4.0,
        }
    )

    assert ket_qua["isWet"] is True
    assert ket_qua["isHeavyRain"] is False
    assert ket_qua["temperatureC"] == 25.0
    assert ket_qua["observedAt"] == "2026-09-11T18:00"


def test_classify_coi_la_mua_to_khi_luong_mua_lon_du_ma_noi_nhe() -> None:
    """Hai nguồn cùng mô tả một hiện tượng — lấy cái nghiêm trọng hơn."""
    ket_qua = weather.classify({"weather_code": 51, "precipitation": 6.0})
    assert ket_qua["isHeavyRain"] is True


def test_classify_khong_no_khi_du_lieu_thieu_hoac_hong() -> None:
    ket_qua = weather.classify({"weather_code": None, "precipitation": "khong-phai-so"})
    assert ket_qua["isWet"] is False
    assert ket_qua["precipitationMm"] == 0.0


# --- Weather: hệ số theo loại địa điểm ----------------------------------------


def test_troi_kho_va_khong_co_du_lieu_deu_khong_doi_gi() -> None:
    assert weather.weather_factor("park", None) == 1.0
    assert weather.weather_factor("park", TROI_KHO) == 1.0


def test_mua_ha_diem_ngoai_troi_nang_diem_trong_nha() -> None:
    assert weather.weather_factor("park", MUA_VUA) == weather.RAIN_OUTDOOR_FACTOR
    assert weather.weather_factor("playground", MUA_VUA) == weather.RAIN_OUTDOOR_FACTOR
    assert weather.weather_factor("cafe", MUA_VUA) == weather.RAIN_INDOOR_FACTOR
    assert weather.weather_factor("shopping_mall", MUA_VUA) == weather.RAIN_INDOOR_FACTOR
    assert weather.weather_factor("cinema", MUA_VUA) == weather.RAIN_INDOOR_FACTOR


def test_mua_to_manh_tay_hon_mua_vua() -> None:
    assert weather.weather_factor("park", MUA_TO) < weather.weather_factor("park", MUA_VUA)


def test_khong_ai_hoan_di_benh_vien_vi_troi_mua() -> None:
    """Nhóm thiết yếu phải trung tính. Hạ điểm bệnh viện lúc mưa là hành vi SAI,
    không phải một tinh chỉnh nhỏ."""
    for category in ("hospital", "pharmacy", "school", "university", "atm"):
        assert weather.weather_factor(category, MUA_TO) == 1.0


def test_loai_khong_nam_trong_bang_thi_trung_tinh() -> None:
    assert weather.weather_factor("loai-la", MUA_TO) == 1.0
    assert weather.weather_factor(None, MUA_TO) == 1.0


def test_moi_loai_that_trong_du_lieu_deu_duoc_phan_nhom() -> None:
    """Bảng che chắn phải phủ hết vốn từ loại THẬT trong CATEGORY_MAP.

    Thiếu một loại thì loại đó im lặng trung tính mãi mãi — không lỗi, không
    log, chỉ là một tín hiệu không bao giờ bật cho nhóm POI đó.
    """
    from app.poi_features import CATEGORY_MAP

    thuc_te = {category for category, _label in CATEGORY_MAP.values()}
    da_phan_nhom = (
        weather.OUTDOOR_CATEGORIES | weather.INDOOR_CATEGORIES | weather.NECESSITY_CATEGORIES
    )
    assert thuc_te - da_phan_nhom == set()


# --- Weather: đường mạng ------------------------------------------------------


class FakeRedis:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.setex_calls: list = []

    def get(self, key: str) -> str | None:
        return self.value

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))


def test_mat_mang_thi_tra_none_chu_khong_no(monkeypatch) -> None:
    # Truyền client rỗng thay vì để `current_weather` tự dựng kết nối Redis:
    # trên máy đang chạy cả stack, Redis thật có sẵn cache từ những lần tìm
    # kiếm trước, nên bài test sẽ nhận giá trị cache và xanh/đỏ tuỳ theo lúc đó
    # docker có bật hay không.
    monkeypatch.setattr(weather, "_fetch_remote", lambda lat, lon: None)
    assert weather.current_weather(HCM_LAT, HCM_LON, client=FakeRedis(None)) is None


def test_cache_key_theo_o_r7_va_khung_gio() -> None:
    key = weather.cache_key(HCM_LAT, HCM_LON, datetime(2026, 9, 11, 17, 45, tzinfo=ZoneInfo("UTC")))
    assert key.startswith("nearby:weather:")
    assert key.endswith(":2026091117")


def test_hai_toa_do_cung_o_r7_dung_chung_mot_cache() -> None:
    """Nếu không thì mỗi lần người dùng nhích vài chục mét là một lần gọi API."""
    at = datetime(2026, 9, 11, 17, 45, tzinfo=ZoneInfo("UTC"))
    a = weather.cache_key(HCM_LAT, HCM_LON, at)
    b = weather.cache_key(HCM_LAT + 0.002, HCM_LON + 0.002, at)
    assert a == b


def test_cache_trung_thi_khong_goi_mang(monkeypatch) -> None:
    import json

    def khong_duoc_goi(lat, lon):  # pragma: no cover - gọi tới là đã sai
        raise AssertionError("cache trúng mà vẫn gọi mạng")

    monkeypatch.setattr(weather, "_fetch_remote", khong_duoc_goi)
    client = FakeRedis(json.dumps(MUA_VUA))

    assert weather.current_weather(HCM_LAT, HCM_LON, client=client) == MUA_VUA


def test_goi_mang_xong_thi_ghi_cache_dung_ttl(monkeypatch) -> None:
    monkeypatch.setattr(weather, "_fetch_remote", lambda lat, lon: MUA_VUA)
    client = FakeRedis(None)

    weather.current_weather(HCM_LAT, HCM_LON, client=client)

    assert len(client.setex_calls) == 1
    assert client.setex_calls[0][1] == weather.CACHE_TTL_SECONDS


# --- Traffic ------------------------------------------------------------------


@pytest.mark.parametrize(
    "gio,phut,cao_diem",
    [(7, 30, True), (8, 29, True), (8, 30, False), (16, 30, True), (18, 59, True), (19, 0, False),
     (12, 0, False), (22, 0, False), (3, 0, False)],
)
def test_khung_gio_cao_diem(gio: int, phut: int, cao_diem: bool) -> None:
    at = datetime(2026, 9, 11, gio, phut, tzinfo=HCM)
    assert traffic.is_peak_hour(at) is cao_diem


def test_gio_cao_diem_lam_cham_lai() -> None:
    cao_diem = datetime(2026, 9, 11, 17, 0, tzinfo=HCM)
    binh_thuong = datetime(2026, 9, 11, 12, 0, tzinfo=HCM)
    assert traffic.peak_speed_ratio(cao_diem) == traffic.PEAK_SPEED_RATIO
    assert traffic.peak_speed_ratio(binh_thuong) == 1.0


def test_mot_o_duy_nhat_thi_khong_phat_ai() -> None:
    """Một điểm dữ liệu không nói lên ô nào đông hơn ô nào."""
    assert traffic.density_penalty("cell-a", {"cell-a": 500}) == 0.0
    assert traffic.density_penalty("cell-a", {}) == 0.0


def test_mat_do_chuan_hoa_theo_o_dong_nhat() -> None:
    counts = {"dong": 100, "vua": 50, "vang": 0}
    assert traffic.density_penalty("dong", counts) == traffic.MAX_DENSITY_PENALTY
    assert traffic.density_penalty("vua", counts) == pytest.approx(
        traffic.MAX_DENSITY_PENALTY * 0.5
    )
    assert traffic.density_penalty("vang", counts) == 0.0
    assert traffic.density_penalty("o-khong-co-trong-bang", counts) == 0.0


def test_he_so_giao_thong_luon_lon_hon_hoac_bang_1() -> None:
    """Hệ số nhân vào THỜI GIAN — giao thông không bao giờ làm đi nhanh hơn."""
    for gio in range(24):
        at = datetime(2026, 9, 11, gio, 0, tzinfo=HCM)
        ket_qua = traffic.traffic_factor(HCM_LAT, HCM_LON, {}, at=at)
        assert ket_qua["factor"] >= 1.0


def test_nguon_he_so_phan_biet_co_mat_do_that_hay_khong() -> None:
    """Log rỗng thì phần mật độ bằng 0 ở khắp nơi. Không ghi ra thì bảng số
    trong báo cáo bị đọc nhầm thành 'mật độ không ảnh hưởng gì'."""
    at = datetime(2026, 9, 11, 12, 0, tzinfo=HCM)
    assert traffic.traffic_factor(HCM_LAT, HCM_LON, {}, at=at)["source"] == "peak-only"
    cell = traffic.traffic_factor(HCM_LAT, HCM_LON, {}, at=at)["cell"]
    assert traffic.traffic_factor(HCM_LAT, HCM_LON, {cell: 3, "khac": 1}, at=at)["source"] == (
        "peak+density"
    )


def test_nguoi_di_bo_khong_bi_ket_xe() -> None:
    eta = {"walk": 30, "motorbike": 8, "car": 7}
    ket_qua = traffic.apply_to_eta(eta, 1.54)

    assert ket_qua["walk"] == 30
    assert ket_qua["motorbike"] > 8
    assert ket_qua["car"] > 7


def test_apply_to_eta_khong_no_khi_thieu_du_lieu() -> None:
    assert traffic.apply_to_eta(None, 1.5) is None
    assert traffic.apply_to_eta({}, 1.5) == {}


# --- Bất biến của enricher ----------------------------------------------------


def make_candidate(category: str) -> dict:
    return {
        "id": f"poi-{category}",
        "category": category,
        "latitude": HCM_LAT,
        "longitude": HCM_LON,
        "distanceMeters": 1_200.0,
        "openingHours": None,
        "timezone": "Asia/Ho_Chi_Minh",
    }


def test_context_score_khong_bao_gio_vuot_khoi_thang_0_1(monkeypatch) -> None:
    """contextScore đi thẳng vào tổng có trọng số của rerank. Một số hạng vượt
    1.0 sẽ đẩy điểm cuối ra khỏi [0,1] — đúng lỗi mà bước sửa rating NULL đã
    phải dọn một lần rồi."""
    monkeypatch.setattr(weather, "current_weather", lambda *a, **k: MUA_TO)
    monkeypatch.setattr(traffic, "ping_density", lambda *a, **k: {})
    monkeypatch.setattr(spatio_temporal, "windowed_popularity", lambda *a, **k: {})

    candidates = [make_candidate(c) for c in ("cafe", "park", "hospital", "shopping_mall")]
    spatio_temporal.enrich_candidates(candidates, latitude=HCM_LAT, longitude=HCM_LON)

    for candidate in candidates:
        assert 0.0 <= candidate["contextScore"] <= 1.0


def test_khong_co_tam_truy_van_thi_thoi_tiet_tat_han(monkeypatch) -> None:
    """Gọi enrich_candidates không kèm toạ độ (đường /recommendations cũ) phải
    cho kết quả y hệt như trước bước B12."""
    def khong_duoc_goi(*a, **k):  # pragma: no cover
        raise AssertionError("gọi thời tiết dù không có tâm truy vấn")

    monkeypatch.setattr(weather, "current_weather", khong_duoc_goi)
    monkeypatch.setattr(traffic, "ping_density", lambda *a, **k: {})
    monkeypatch.setattr(spatio_temporal, "windowed_popularity", lambda *a, **k: {})

    candidates = [make_candidate("park")]
    spatio_temporal.enrich_candidates(candidates)

    assert candidates[0]["weatherFactor"] == 1.0
    assert candidates[0]["weather"] is None


def test_tat_bang_cau_hinh_thi_khong_goi_gi(monkeypatch) -> None:
    def khong_duoc_goi(*a, **k):  # pragma: no cover
        raise AssertionError("đã tắt bằng cấu hình mà vẫn gọi")

    monkeypatch.setattr(settings, "weather_enabled", False, raising=False)
    monkeypatch.setattr(settings, "traffic_enabled", False, raising=False)
    monkeypatch.setattr(weather, "current_weather", khong_duoc_goi)
    monkeypatch.setattr(traffic, "ping_density", khong_duoc_goi)
    monkeypatch.setattr(spatio_temporal, "windowed_popularity", lambda *a, **k: {})

    candidates = [make_candidate("cafe")]
    spatio_temporal.enrich_candidates(candidates, latitude=HCM_LAT, longitude=HCM_LON)

    assert candidates[0]["weatherFactor"] == 1.0


# --- Hệ số vòng vèo (lộ trình B16, bước rẻ) ------------------------------------


def test_eta_khong_con_dung_duong_chim_bay() -> None:
    """Thiếu hệ số vòng vèo thì mọi ETA lạc quan khoảng 46% và người dùng đến
    muộn — sai số không lộ ra ở đâu vì con số trông vẫn rất hợp lý."""
    assert spatio_temporal.DETOUR_FACTOR > 1.0

    km_thang = 10.0
    eta = spatio_temporal.eta_minutes(km_thang * 1000)
    thang_tuy = round(km_thang / 22.0 * 60)  # nếu tính theo đường thẳng
    assert eta["motorbike"] > thang_tuy


def test_he_so_vong_veo_khop_voi_so_do_that() -> None:
    """1.4625 là TRUNG VỊ đo được trên 477 cặp POI thật qua OSRM, không phải số
    kinh nghiệm. Đổi nó thì phải đo lại bằng scripts/measure_detour.py rồi cập
    nhật cả con số lẫn ghi chú, nếu không báo cáo sẽ trích một con số không có
    nguồn."""
    assert spatio_temporal.DETOUR_FACTOR == 1.4625


def test_uoc_luong_bam_sat_tuyen_that_da_do() -> None:
    """Đối chứng với một tuyến ĐÃ ĐO bằng OSRM: Cà phê Bến Nghé, chim bay 314 m,
    đường thật 465,2 m. Ước lượng phải nằm trong khoảng 10% của số thật."""
    uoc_luong = 314.0 * spatio_temporal.DETOUR_FACTOR
    assert abs(uoc_luong - 465.2) / 465.2 < 0.10


def test_moi_phuong_tien_deu_chiu_cung_he_so() -> None:
    eta = spatio_temporal.eta_minutes(5_000)
    assert eta["walk"] > eta["motorbike"] >= eta["car"]
