"""Phase 10.4 — khoá lại các case retrieval đã đo được thật qua API sống.

Ba truy vấn này đến từ ``backend/tests/eval/production_queries.json`` (Phase
10 production evaluation, đo 2026-09-12). Cả ba đều là lỗi mà soft relevance
gate ở Phase 6.5 (``ranking._is_text_relevant``) KHÔNG sửa được, vì các
candidate sai đều CÓ ``bm25Score`` thật (>0) — không phải candidate "không tín
hiệu văn bản" mà gate đó nhắm tới.

Cập nhật 2026-09-13: "cơm tấm" đã sửa xong (xem docstring của test tương ứng —
nguyên nhân hoá ra là thiếu ``minimum_should_match`` chứ không phải chỉ do
asciifolding). "bệnh viện" còn xfail: BM25 nay đã đúng, phần lật ngược thứ hạng
chuyển sang tín hiệu rating/popularity của dữ liệu seed.

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
        "CHƯA HẾT, nhưng đã thu hẹp và đã XÁC NHẬN nguyên nhân (đo lại "
        "2026-09-13, sau khi dựng lại chỉ mục để field `.strict` của Phase 10 "
        "thực sự có hiệu lực — trước đó chỉ mục đang chạy KHÔNG có field này "
        "nên bản sửa analyzer chưa bao giờ tác dụng). "
        "Đo được: 'Bệnh Viện Mắt Sài Gòn' có textScore = 1.0000, tức BM25 CAO "
        "NHẤT tập ứng viên — `.strict` ĐÃ làm đúng việc của nó. 'Công viên Bến "
        "Bạch Đằng' vẫn được 0.7893 vì `category_label` 'công viên' fold thành "
        "token `vien`, trùng với 'bệnh viện'. Phần lật ngược thứ hạng KHÔNG "
        "còn là BM25 nữa mà là rating 4.6 + popularity 0.96 của công viên "
        "(dữ liệu seed): hai tín hiệu đó cho +0.158 điểm chuẩn hoá, lợi thế "
        "văn bản của bệnh viện chỉ +0.052. "
        "Cổng chất lượng (`settings.ranking_quality_gate_exponent`) đưa bệnh "
        "viện từ hạng 7 lên hạng 3-4 và bão hoà ở đó; `minimum_should_match` "
        "góp thêm một bậc. Muốn hạng 1 thì cần rating/review THẬT cho POI y tế "
        "(hiện `rating=null`) hoặc train lại LTR trên click người dùng thật — "
        "xem mục 3 của tài liệu bàn giao. KHÔNG sửa bằng cách hạ tay trọng số "
        "rating cho tới khi có dữ liệu thật."
    ),
)
def test_benh_vien_khong_bi_cong_vien_bm25_gia_vuot_mat() -> None:
    results = _search("bệnh viện")
    assert results, "Không có kết quả nào cho 'bệnh viện'"
    assert results[0]["categoryLabel"] != "Công viên"


def test_com_tam_khong_bi_le_van_tam_bm25_gia_vuot_mat() -> None:
    """ĐÃ SỬA 2026-09-13 — marker xfail gỡ đi vì test đã xpass.

    Nguyên nhân thật KHÔNG phải chỉ do asciifolding gộp 'Tấm'/'Tám' như đã
    nghi. Sau khi dựng lại chỉ mục cho field `.strict` có hiệu lực, hạng 1 vẫn
    sai — nhưng là 'Tâm Silk' (shop lụa), và top 5 còn có 'Commonwealth Bank'
    lẫn 'Cộng Cà Phê'. Hai thứ đó lộ ra hai lỗi thật của câu truy vấn BM25:

    1. `operator: "or"` mà KHÔNG đặt `minimum_should_match` — khớp 1 trong 2
       token là đủ. 'Tâm Silk' khớp mỗi `tam`, và BM25 chuẩn hoá theo độ dài
       field nên cái tên 2 token được thưởng đậm hơn hẳn 'Quán Cơm Tấm Hoàng
       Minh' dù quán này khớp CẢ HAI token.
    2. `fuzziness: "AUTO"` cho phép sửa 1 ký tự với token dài 3-5, mà tiếng
       Việt đơn âm nên `com` khớp mờ sang `cong` ('Cộng Cà Phê') và `con`.

    Sửa bằng `settings.search_text_min_should_match = "2<70%"`: từ 2 token trở
    xuống bắt buộc khớp hết. Đo lại 3 lần liên tiếp, ổn định: hạng của quán cơm
    tấm đi từ 2 lên 1, top 5 thành 'Quán Cơm Tâm Mộc', 'Tâm Silk', 'Quán Ăn Cô
    Tấm', 'Quán Cơm Tấm Hoàng Minh', 'Cơm Tấm Cali' — ngân hàng và quán cà phê
    khớp mờ đã biến mất. Ba truy vấn đã gán nhãn (cà phê/công viên/bảo tàng)
    giữ nguyên hạng 1.
    """
    results = _search("cơm tấm")
    assert results, "Không có kết quả nào cho 'cơm tấm'"
    assert results[0]["name"] != "Công viên Lê Văn Tám"
