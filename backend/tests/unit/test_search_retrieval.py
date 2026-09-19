from app.config import settings
from app.search import retrieval
from app.search.client import is_search_configured, reset_client_cache


# --- _gate_by_text_relevance ---------------------------------------------------
#
# Đo được thật (không phải giả định): truy vấn "bệnh viện" trả "Phở Nhà Mình"
# ở rank 0 — không qua BM25/vector, chỉ vì gần + đang trending. Bốn test dưới
# khoá lại đúng hành vi phải có sau khi vá.


def test_gate_bo_candidate_chi_geo_trending_khi_da_co_candidate_khop_chu() -> None:
    # RRF xếp "gan-trending" (chỉ geo+trending) TRƯỚC "lien-quan" (có bm25) —
    # đúng tình huống lỗi đã đo được. Candidate không khớp chữ phải bị bỏ hẳn,
    # không chỉ bị đẩy xuống (danh sách bị độn đủ 50 bằng quán ăn/trường học).
    fused = [("gan-trending", 0.05), ("lien-quan", 0.03)]
    channels = {"bm25": ["lien-quan"], "geo": ["gan-trending", "lien-quan"], "trending": ["gan-trending"]}

    gated = retrieval._gate_by_text_relevance(fused, channels, "benh vien")

    assert [poi_id for poi_id, _ in gated] == ["lien-quan"]


def test_gate_khong_tinh_vector_la_bang_chung_lien_quan() -> None:
    """k-NN trên embedding băm luôn trả đủ k hit kể cả cosine ~ 0 — candidate
    chỉ có ở kênh vector (vd. "Công viên" cho "bệnh viện") không được giữ."""
    fused = [("cong-vien", 0.06), ("benh-vien", 0.04)]
    channels = {"bm25": ["benh-vien"], "vector": ["cong-vien", "benh-vien"], "geo": ["cong-vien"]}

    gated = retrieval._gate_by_text_relevance(fused, channels, "benh vien")

    assert [poi_id for poi_id, _ in gated] == ["benh-vien"]


def test_gate_giu_nguyen_thu_tu_rrf_trong_nhom_khop_chu() -> None:
    fused = [("b", 0.5), ("a", 0.4), ("d", 0.3), ("c", 0.2)]
    channels = {"bm25": ["a", "b"], "geo": ["c", "d"]}

    gated = retrieval._gate_by_text_relevance(fused, channels, "ca phe")

    assert [poi_id for poi_id, _ in gated] == ["b", "a"]


def test_gate_khong_ap_dung_khi_khong_co_query_text() -> None:
    """Duyệt theo vị trí thuần (không gõ chữ) — geo là kênh chính đáng, không gate."""
    fused = [("gan-trending", 0.05), ("lien-quan", 0.03)]
    channels = {"bm25": ["lien-quan"], "geo": ["gan-trending", "lien-quan"]}

    gated = retrieval._gate_by_text_relevance(fused, channels, "")

    assert gated == fused


def test_gate_khong_doi_gi_khi_khong_co_candidate_lien_quan() -> None:
    """BM25/vector không trả candidate nào (chỉ trending/geo) — không có gì để
    ưu tiên, giữ nguyên thứ tự RRF thay vì đẩy hết vào "supplementary"."""
    fused = [("gan-trending", 0.05), ("khac", 0.03)]
    channels = {"bm25": [], "geo": ["gan-trending", "khac"]}

    gated = retrieval._gate_by_text_relevance(fused, channels, "mot truy van hiem")

    assert gated == fused


def test_multi_channel_returns_none_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "opensearch_url", "", raising=False)
    reset_client_cache()

    result = retrieval.multi_channel_candidates(10.77, 106.70, 3000, "cà phê", None, 100)

    assert result is None


def test_multi_channel_returns_none_when_backend_forced_postgis(monkeypatch) -> None:
    monkeypatch.setattr(settings, "search_backend", "postgis", raising=False)
    monkeypatch.setattr(settings, "opensearch_url", "http://localhost:9200", raising=False)
    reset_client_cache()

    assert is_search_configured() is False
    assert retrieval.multi_channel_candidates(10.0, 106.0, 1000, None, None, 50) is None


def test_multi_channel_falls_back_when_cluster_unreachable(monkeypatch) -> None:
    # Cấu hình trỏ tới cổng không có ai lắng nghe -> ping fail -> None (fallback).
    monkeypatch.setattr(settings, "search_backend", "auto", raising=False)
    monkeypatch.setattr(settings, "opensearch_url", "http://127.0.0.1:1", raising=False)
    reset_client_cache()

    assert retrieval.multi_channel_candidates(10.0, 106.0, 1000, "phở", None, 50) is None

    reset_client_cache()
