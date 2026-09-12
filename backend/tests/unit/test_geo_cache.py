"""Kiểm thử Geo-Spatial Cache: trending có khung giờ, theo ô H3, và GEOSEARCH.

Ba lỗi mà bộ test này canh, cả ba đều thuộc loại "chạy được nhưng sai":

1. Bộ đếm không có TTL — trending đóng băng ở POI cũ sau vài ngày.
2. Trending không có địa điểm — quán hot ở Quận 1 cộng điểm cho người ở Thủ Đức.
3. Toạ độ GPS giả lọt vào bộ đếm khu vực.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import h3
import pytest

from app import geo_cache, ranking

HCM_LAT, HCM_LON = 10.7757, 106.7009
THU_DUC_LAT, THU_DUC_LON = 10.8700, 106.7800  # cách ~12 km, chắc chắn khác ô r8

AT = datetime(2026, 9, 11, 17, 30, tzinfo=timezone.utc)


class FakePipeline:
    def __init__(self, store: dict, ttl: dict) -> None:
        self.store = store
        self.ttl = ttl
        self.queued: list = []

    def zincrby(self, key: str, amount: float, member: str) -> None:
        self.queued.append(("zincrby", key, amount, member))

    def expire(self, key: str, seconds: int) -> None:
        self.queued.append(("expire", key, seconds))

    def zrange(self, key: str, start: int, end: int, withscores: bool = False) -> None:
        self.queued.append(("zrange", key))

    def geoadd(self, key: str, values) -> None:
        self.queued.append(("geoadd", key, values))

    def hset(self, *args, **kwargs) -> None:
        self.queued.append(("hset",))

    def execute(self) -> list:
        results: list = []
        for item in self.queued:
            if item[0] == "zincrby":
                _, key, amount, member = item
                bucket = self.store.setdefault(key, {})
                bucket[member] = bucket.get(member, 0.0) + amount
                results.append(bucket[member])
            elif item[0] == "expire":
                _, key, seconds = item
                self.ttl[key] = seconds
                results.append(True)
            elif item[0] == "zrange":
                _, key = item
                results.append(sorted(self.store.get(key, {}).items(), key=lambda kv: kv[1]))
            else:
                results.append(True)
        self.queued = []
        return results


class FakeRedis:
    """Chỉ đủ lệnh cho geo_cache: pipeline, zrange, geosearch."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}
        self.ttl: dict[str, int] = {}
        self.geo: dict[str, tuple[float, float]] = {}  # member -> (lon, lat)

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self.store, self.ttl)

    def ping(self) -> bool:
        return True

    def geosearch(self, key, longitude, latitude, radius, unit, withcoord=False):
        out = []
        for member, (lon, lat) in self.geo.items():
            if geo_cache.haversine_meters(latitude, longitude, lat, lon) <= radius:
                out.append([member, (lon, lat)] if withcoord else member)
        return out


def write(client: FakeRedis, poi_id: str, weight: float, lat=None, lon=None, at=AT) -> None:
    pipeline = client.pipeline(transaction=False)
    geo_cache.record_poi_interaction(pipeline, poi_id, weight, lat, lon, at=at)
    pipeline.execute()


# --- (b) bộ đếm biết quên ------------------------------------------------------


def test_moi_bo_dem_deu_nam_trong_khung_gio_va_co_ttl() -> None:
    """Không có TTL thì điểm cộng dồn vĩnh viễn và trending đóng băng ở POI cũ."""
    client = FakeRedis()
    write(client, "poi-1", 1.0, HCM_LAT, HCM_LON)

    assert geo_cache.hour_bucket(AT) == "2026091117"
    assert all(key.endswith("2026091117") for key in client.store)
    assert client.ttl and all(ttl == geo_cache.BUCKET_TTL_SECONDS for ttl in client.ttl.values())


def test_gop_ba_khung_gio_theo_he_so_1_0_5_0_25() -> None:
    """Giờ hiện tại tính đủ, giờ trước một nửa, giờ trước nữa một phần tư."""
    client = FakeRedis()
    write(client, "poi-1", 4.0, at=AT)
    write(client, "poi-1", 4.0, at=AT - timedelta(hours=1))
    write(client, "poi-1", 4.0, at=AT - timedelta(hours=2))

    scores = geo_cache.trending_pois(client=client, at=AT)

    assert scores["poi-1"] == pytest.approx(4.0 * 1.0 + 4.0 * 0.5 + 4.0 * 0.25)


def test_khung_gio_ngoai_cua_so_khong_duoc_tinh() -> None:
    """Sự kiện 5 giờ trước không còn là 'trending' dù key chưa hết hạn."""
    client = FakeRedis()
    write(client, "poi-cu", 99.0, at=AT - timedelta(hours=5))
    write(client, "poi-moi", 1.0, at=AT)

    scores = geo_cache.trending_pois(client=client, at=AT)

    assert "poi-cu" not in scores
    assert scores["poi-moi"] == pytest.approx(1.0)


# --- (a) trending có địa điểm --------------------------------------------------


def test_trending_ghi_vao_o_h3_cua_nguoi_dung() -> None:
    client = FakeRedis()
    cell = write_and_return_cell(client)
    assert cell == h3.latlng_to_cell(HCM_LAT, HCM_LON, geo_cache.TRENDING_RESOLUTION)
    assert geo_cache.hex_key(cell, "2026091117") in client.store


def write_and_return_cell(client: FakeRedis) -> str:
    pipeline = client.pipeline(transaction=False)
    cell = geo_cache.record_poi_interaction(pipeline, "poi-1", 1.0, HCM_LAT, HCM_LON, at=AT)
    pipeline.execute()
    return cell


def test_quan_hot_o_quan_1_khong_cong_diem_cho_nguoi_o_thu_duc() -> None:
    """Đây chính là lỗi mà bước B6a sửa, viết thẳng thành một bài test."""
    client = FakeRedis()
    write(client, "quan-tra-sua-q1", 50.0, HCM_LAT, HCM_LON)

    gan = geo_cache.trending_pois_near(HCM_LAT, HCM_LON, client=client, at=AT)
    xa = geo_cache.trending_pois_near(THU_DUC_LAT, THU_DUC_LON, client=client, at=AT)

    assert gan["quan-tra-sua-q1"] == pytest.approx(50.0)
    assert xa == {}


def test_su_kien_khong_co_toa_do_van_vao_bang_toan_cuc() -> None:
    """Không có toạ độ thì mất bảng khu vực, nhưng không được mất luôn tín hiệu."""
    client = FakeRedis()
    cell = None
    pipeline = client.pipeline(transaction=False)
    cell = geo_cache.record_poi_interaction(pipeline, "poi-1", 2.0, None, None, at=AT)
    pipeline.execute()

    assert cell is None
    assert geo_cache.trending_pois(client=client, at=AT)["poi-1"] == pytest.approx(2.0)
    assert geo_cache.trending_pois_near(HCM_LAT, HCM_LON, client=client, at=AT) == {}


# --- phía đọc trong ranking ----------------------------------------------------


def test_apply_trending_boost_uu_tien_khu_vuc_roi_ve_toan_cuc(monkeypatch) -> None:
    client = FakeRedis()
    monkeypatch.setattr(geo_cache, "get_client", lambda: client)
    write(client, "poi-gan", 10.0, HCM_LAT, HCM_LON, at=datetime.now(timezone.utc))

    candidates = [{"id": "poi-gan"}, {"id": "poi-khac"}]
    ranking.apply_trending_boost(candidates, HCM_LAT, HCM_LON)

    assert candidates[0]["trendingScore"] > 0
    assert candidates[0]["trendingScope"] == "hex"
    assert candidates[1]["trendingScore"] == 0.0


def test_trending_scope_phan_biet_khu_vuc_vang_voi_redis_chet(monkeypatch) -> None:
    """0 vì khu vực vắng và 0 vì Redis chết phải nhìn ra được là hai chuyện khác nhau."""
    monkeypatch.setattr(geo_cache, "get_client", lambda: None)
    candidates = [{"id": "poi-1"}]

    ranking.apply_trending_boost(candidates, HCM_LAT, HCM_LON)

    assert candidates[0]["trendingScore"] == 0.0
    assert candidates[0]["trendingScope"] == "empty"


# --- (c) GEOSEARCH -------------------------------------------------------------


def test_geosearch_dem_phien_dang_hoat_dong_quanh_tung_poi(monkeypatch) -> None:
    client = FakeRedis()
    monkeypatch.setattr(geo_cache, "get_client", lambda: client)
    # Ba phiên sát tâm, một phiên cách ~1,1 km.
    client.geo = {
        "s1": (HCM_LON, HCM_LAT),
        "s2": (HCM_LON + 0.0005, HCM_LAT),
        "s3": (HCM_LON, HCM_LAT + 0.0005),
        "s-xa": (HCM_LON + 0.01, HCM_LAT),
    }
    candidates = [
        {"id": "poi-tam", "latitude": HCM_LAT, "longitude": HCM_LON},
        {"id": "poi-xa", "latitude": HCM_LAT, "longitude": HCM_LON + 0.01},
    ]

    ranking.apply_crowd_signal(candidates, HCM_LAT, HCM_LON, 3_000)

    assert candidates[0]["liveNearbyUsers"] == 3
    assert candidates[1]["liveNearbyUsers"] == 1


def test_crowd_signal_tra_0_khi_redis_khong_co(monkeypatch) -> None:
    monkeypatch.setattr(geo_cache, "get_client", lambda: None)
    candidates = [{"id": "poi-1", "latitude": HCM_LAT, "longitude": HCM_LON}]

    ranking.apply_crowd_signal(candidates, HCM_LAT, HCM_LON, 3_000)

    assert candidates[0]["liveNearbyUsers"] == 0


def test_geosearch_doc_dung_thu_tu_lon_lat() -> None:
    """Redis trả (kinh độ, vĩ độ) — ngược với quy ước (lat, lon) của phần còn lại.

    Đọc nhầm thứ tự không gây lỗi, chỉ làm mọi phiên rơi xuống giữa Ấn Độ Dương
    và tín hiệu độ đông luôn bằng 0. Đúng kiểu lỗi im lặng.
    """
    client = FakeRedis()
    client.geo = {"s1": (HCM_LON, HCM_LAT)}

    points = geo_cache.active_session_points(HCM_LAT, HCM_LON, 500, client=client)

    assert points == [(HCM_LAT, HCM_LON)]
