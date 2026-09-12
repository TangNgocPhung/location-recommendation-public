"""Kiểm thử kênh không gian bằng vành hexagon H3.

Câu hỏi trọng tâm không phải "hàm có chạy không" mà là **vành có phủ hết hình
tròn không**. Thiếu phủ là lỗi im lặng điển hình: truy vấn vẫn trả kết quả, vẫn
trông hợp lý, chỉ là một số POI trong bán kính không bao giờ lọt vào ứng viên —
và nDCG tụt mà không ai biết vì sao.
"""

from __future__ import annotations

import math
import random

import h3
import pytest

from app.config import settings
from app import poi_features
from app.search import query as query_builder, retrieval

HCM_LAT, HCM_LON = 10.7757, 106.7009


def offset_point(lat: float, lon: float, distance_m: float, bearing_rad: float):
    """Dời một điểm đi ``distance_m`` theo hướng ``bearing_rad`` (xấp xỉ phẳng)."""
    d_lat = (distance_m * math.cos(bearing_rad)) / 111_320.0
    d_lon = (distance_m * math.sin(bearing_rad)) / (111_320.0 * math.cos(math.radians(lat)))
    return lat + d_lat, lon + d_lon


@pytest.mark.parametrize("radius", [100, 300, 800, 1500, 3000, 5000, 10000])
def test_vanh_phu_het_moi_diem_trong_ban_kinh(radius: int) -> None:
    """Không một điểm nào trong hình tròn rơi ra ngoài vành hexagon.

    Đây là bất biến sống còn của kênh này. Vành phủ DƯ thì vô hại (PostGIS cắt
    lại bằng ST_DWithin ở hydrate), nhưng phủ THIẾU thì mất recall vĩnh viễn.
    """
    coverage = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, radius)
    assert coverage is not None
    cells = set(coverage.cells)

    rng = random.Random(radius)
    for _ in range(2_000):
        # sqrt() để mẫu rải đều theo DIỆN TÍCH, nếu không thì điểm dồn về tâm
        # và bài kiểm thử bỏ sót đúng phần vành ngoài — chỗ dễ hụt nhất.
        distance = radius * math.sqrt(rng.random())
        lat, lon = offset_point(HCM_LAT, HCM_LON, distance, rng.random() * 2 * math.pi)
        assert h3.latlng_to_cell(lat, lon, coverage.resolution) in cells


def test_diem_ngoai_ban_kinh_xa_thi_khong_nam_trong_vanh() -> None:
    """Vành phải phủ dư có giới hạn, không phủ cả thành phố.

    Nếu bài này hỏng thì kênh H3 đang trả về gần như toàn bộ chỉ mục và việc
    "lọc" chỉ còn trên danh nghĩa.
    """
    coverage = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 1_000)
    assert coverage is not None
    cells = set(coverage.cells)

    far_lat, far_lon = offset_point(HCM_LAT, HCM_LON, 8_000, 0.0)
    assert h3.latlng_to_cell(far_lat, far_lon, coverage.resolution) not in cells


def test_ban_kinh_nho_dung_do_phan_giai_min_hon() -> None:
    """Bán kính càng nhỏ thì độ phân giải càng mịn — vành bám sát hình tròn hơn."""
    nho = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 500)
    lon_ = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 5_000)
    assert nho is not None and lon_ is not None
    assert nho.resolution > lon_.resolution


def test_so_o_khong_bao_gio_vuot_tran() -> None:
    for radius in (100, 1_000, 5_000, 15_000):
        coverage = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, radius, max_cells=512)
        if coverage is not None:
            assert len(coverage.cells) <= 512


def test_ban_kinh_qua_lon_tra_none_thay_vi_truy_van_khong_lo() -> None:
    """50 km là trần của SearchRequest. Ở mức đó vành hexagon hết rẻ hơn
    geo_distance, nên phải trả None để gọi bên rơi về lọc khoảng cách."""
    assert poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 50_000, max_cells=512) is None


def test_cell_count_khop_voi_so_o_thuc_te() -> None:
    """Công thức 3k²+3k+1 phải khớp với grid_disk thật, nếu không việc chọn độ
    phân giải theo trần số ô sẽ dựa trên một con số bịa."""
    origin = h3.latlng_to_cell(HCM_LAT, HCM_LON, 9)
    for k in range(0, 8):
        assert poi_features.h3_ring_cell_count(k) == len(h3.grid_disk(origin, k))


def test_query_body_loc_bang_terms_chu_khong_phai_geo_distance() -> None:
    """Điểm mấu chốt của cả gói: lọc là tra chỉ mục đảo, không phải tính hình học."""
    coverage = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 1_000)
    assert coverage is not None

    body = query_builder.h3_body(
        coverage.cells, coverage.field, HCM_LAT, HCM_LON, None, 150
    )
    filters = body["query"]["bool"]["filter"]
    assert any("terms" in clause for clause in filters)
    assert not any("geo_distance" in clause for clause in filters)
    assert filters[0]["terms"]["h3_r9"] == list(coverage.cells)
    # Vẫn sắp xếp theo khoảng cách thật: H3 chọn ai vào, toạ độ chọn ai trên.
    assert "_geo_distance" in body["sort"][0]


def test_query_body_giu_loc_category() -> None:
    coverage = poi_features.h3_ring_ids(HCM_LAT, HCM_LON, 1_000)
    assert coverage is not None
    body = query_builder.h3_body(
        coverage.cells, coverage.field, HCM_LAT, HCM_LON, "cafe", 150
    )
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"category_label.raw": "cafe"}} in filters


class FakeClient:
    """Ghi lại body đã gửi để kiểm tra kênh nào thực sự được dùng."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def search(self, index: str, body: dict) -> dict:
        self.bodies.append(body)
        return {"hits": {"hits": [{"_source": {"poi_id": "poi-1"}, "_score": 1.0}]}}


def test_che_do_both_chay_ca_hai_kenh(monkeypatch) -> None:
    """Mặc định lộ trình B3: H3 là kênh riêng, geo_distance vẫn còn làm lọc tinh."""
    monkeypatch.setattr(settings, "geo_channel", "both", raising=False)
    client = FakeClient()

    channels, info = retrieval._spatial_channels(client, HCM_LAT, HCM_LON, 2_000, None)

    assert set(channels) == {"geo", "h3"}
    assert info["geoChannelMode"] == "both"
    assert info["h3CellCount"] > 0
    filters = [f for body in client.bodies for f in body["query"]["bool"]["filter"]]
    assert any("terms" in clause for clause in filters)
    assert any("geo_distance" in clause for clause in filters)


def test_che_do_h3_tat_han_geo_distance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "geo_channel", "h3", raising=False)
    client = FakeClient()

    channels, _info = retrieval._spatial_channels(client, HCM_LAT, HCM_LON, 2_000, None)

    assert set(channels) == {"h3"}
    # Chỉ xét mệnh đề LỌC. Body H3 vẫn sắp xếp theo `_geo_distance` — sắp xếp
    # không phải lọc, và đó chính là thiết kế: H3 chọn ai vào, toạ độ chọn ai trên.
    filters = [f for body in client.bodies for f in body["query"]["bool"]["filter"]]
    assert not any("geo_distance" in clause for clause in filters)
    assert any("terms" in clause for clause in filters)


def test_che_do_geo_distance_tat_han_h3(monkeypatch) -> None:
    monkeypatch.setattr(settings, "geo_channel", "geo_distance", raising=False)
    client = FakeClient()

    channels, _info = retrieval._spatial_channels(client, HCM_LAT, HCM_LON, 2_000, None)

    assert set(channels) == {"geo"}
    filters = [f for body in client.bodies for f in body["query"]["bool"]["filter"]]
    assert not any("terms" in clause for clause in filters)
    assert any("geo_distance" in clause for clause in filters)


def test_ban_kinh_qua_lon_ghi_lai_ly_do_bo_h3(monkeypatch) -> None:
    """Bỏ kênh H3 phải GHI LẠI lý do. Nếu im lặng, một bài đo trên bán kính lớn
    sẽ bị ghi công cho H3 trong khi thực tế chỉ có geo_distance chạy."""
    monkeypatch.setattr(settings, "geo_channel", "both", raising=False)
    monkeypatch.setattr(settings, "h3_ring_max_cells", 512, raising=False)
    client = FakeClient()

    channels, info = retrieval._spatial_channels(client, HCM_LAT, HCM_LON, 50_000, None)

    assert set(channels) == {"geo"}
    assert info["h3Skipped"] == "radius-too-large"


def test_tong_trong_so_khong_gian_khong_doi_so_voi_truoc_B3() -> None:
    """Chia 0.6 thành geo 0.35 + h3 0.25 chứ không cộng thêm kênh mới.

    Nếu bài này hỏng, chiều không gian đã nặng hơn lúc đo Phase 3 và mọi so
    sánh nDCG trước/sau H3 đang đo nhầm trọng số thay vì đo cách lọc.
    """
    weights = retrieval.CHANNEL_WEIGHTS
    assert round(weights["geo"] + weights["h3"], 6) == 0.6
    assert weights["geo"] > weights["h3"]
