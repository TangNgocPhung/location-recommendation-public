"""Phase 10.4 — khoá lại các case retrieval đã đo được thật qua API sống.

Ba truy vấn này đến từ ``backend/tests/eval/production_queries.json`` (Phase
10 production evaluation, đo 2026-09-12). Hai bug xfail dưới đây là lỗi TẦNG
BM25/analyzer (``vi_folded``/asciifolding gộp token), không phải lỗi ranking —
soft relevance gate ở Phase 6.5 (``ranking._is_text_relevant``) không sửa được
vì các candidate sai này CÓ ``bm25Score`` thật (>0), không phải candidate
"không tín hiệu văn bản" mà gate nhắm tới.

Quy trình đã thống nhất: khoá test trước (file này) -> soi token qua
``_analyze`` của OpenSearch -> chỉ sửa analyzer nếu xác định đúng nguyên nhân
-> chạy lại toàn bộ test + regression queries -> mới đóng Phase 10. KHÔNG sửa
analyzer chỉ vì 2 truy vấn — nó là thành phần nền, ảnh hưởng cả BM25 lẫn phân
phối dữ liệu train LTR.
"""

import os

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
CENTER = {"latitude": 10.7769, "longitude": 106.6951, "radius": 3000}


def _search(query: str, limit: int = 10) -> list[dict]:
    response = httpx.post(
        f"{API_BASE_URL}/api/v1/search",
        timeout=15,
        json={**CENTER, "query": query, "limit": limit},
    )
    assert response.status_code == 200
    return response.json()["results"]


def test_ca_phe_gan_day_khong_con_dung_dau_boi_bun_rieu() -> None:
    """ĐÃ SỬA ở Phase 6.5 (soft relevance gate, xem `ranking._is_text_relevant`).

    Đo được thật TRƯỚC khi sửa: "Bún riêu Quận 4" (không tín hiệu văn bản
    liên quan "cà phê", chỉ gần + trending) đứng hạng 1. Khoá lại ở đây để bất
    kỳ thay đổi retrieval/ranking nào sau này làm bug quay lại sẽ bị bắt ngay,
    không phải chờ tới lần soi thủ công tiếp theo."""
    results = _search("cà phê gần đây")
    assert results, "Không có kết quả nào cho 'cà phê gần đây'"
    assert results[0]["categoryLabel"] == "Cà phê"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Lỗi TẦNG BM25/analyzer, không phải ranking — đo được thật (Phase 10, "
        "2026-09-12): 'Công viên Bến Bạch Đằng' (bm25=15.6) và 'Công viên Lê "
        "Văn Tám' (bm25=9.15) đứng TRÊN cả bệnh viện thật ('Bệnh viện Bình "
        "Dân', 'Bệnh Viện Mắt Sài Gòn') cho truy vấn 'bệnh viện', ở CẢ HAI "
        "ranker (linear và ltr) — vì hai công viên này có bm25Score THẬT > 0, "
        "không phải candidate 'chỉ geo/trending' mà soft relevance gate của "
        "Phase 6.5 nhắm demote. Nghi vấn: analyzer `vi_folded` (asciifolding) "
        "gộp nhầm token giữa 'bệnh viện' và tên/mô tả hai công viên này — "
        "cùng họ lỗi với case 'cơm tấm' bên dưới, chưa xác nhận nguyên nhân "
        "chính xác qua `_analyze`, CHƯA sửa. Test XFAIL có chủ đích: khi "
        "analyzer được sửa đúng, test sẽ tự PASS (xpass) — đó là tín hiệu để "
        "xoá marker này, không phải để sửa ranking/LTR."
    ),
)
def test_benh_vien_khong_bi_cong_vien_bm25_gia_vuot_mat() -> None:
    results = _search("bệnh viện")
    assert results, "Không có kết quả nào cho 'bệnh viện'"
    assert results[0]["categoryLabel"] != "Công viên"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Lỗi TẦNG BM25/analyzer, không phải ranking — đo được thật (Phase 10, "
        "2026-09-12): 'Công viên Lê Văn Tám' (bm25=11.89, bm25 THẬT > 0) đứng "
        "hạng 1 cho truy vấn 'cơm tấm' ở CẢ HAI ranker. Nghi vấn: analyzer "
        "`vi_folded` (asciifolding) xoá dấu THANH ĐIỆU chứ không chỉ dấu gốc, "
        "khiến 'Tấm' (cơm tấm) và 'Tám' (Lê Văn Tám) cùng fold về một token "
        "('tam') — chưa xác nhận qua `_analyze`, CHƯA sửa. Cùng nguyên nhân "
        "nghi vấn với case 'bệnh viện' ở trên. XFAIL có chủ đích, xem lý do "
        "đầy đủ ở test đó."
    ),
)
def test_com_tam_khong_bi_le_van_tam_bm25_gia_vuot_mat() -> None:
    results = _search("cơm tấm")
    assert results, "Không có kết quả nào cho 'cơm tấm'"
    assert results[0]["name"] != "Công viên Lê Văn Tám"
