"""Kiểm thử tầng Learning-to-Rank.

Trọng tâm là những chỗ hỏng ÂM THẦM: thiếu dữ liệu bị nhầm thành dữ liệu xấu,
mô hình lệch cột so với danh sách đặc trưng, và đường phục vụ gãy khi không có
file mô hình. Ba lỗi này đều không làm chương trình dừng — chúng chỉ làm số
liệu sai.
"""

from __future__ import annotations

import json
import math

import pytest

from app import ranking
from app.ltr import model as ltr_model
from app.ltr.features import FEATURE_NAMES, coverage, extract_features, feature_row


def make_candidate(**overrides):
    base = {
        "id": "poi-1",
        "category": "cafe",
        "distanceMeters": 500.0,
        "textScore": 0.8,
        "bm25Score": 12.5,
        "fusionScoreNorm": 0.9,
        "retrievalChannels": ["bm25", "geo"],
        "rating": 4.5,
        "ratingSource": "user",
        "reviewCount": 100,
        "popularityScore": 0.7,
        "priceLevel": 2,
        "trendingScore": 0.1,
        "recencyScore": 0.2,
        "regionCtr": 0.05,
        "openNow": True,
        "closesInMinutes": 120,
        "opensInMinutes": None,
        "contextScore": 0.8,
        "timeContext": {"hour": 9, "isWeekend": False, "categoryMatchesTime": 1.0},
        "popularityWindows": {"w15": 1, "w1h": 3, "w24h": 10},
    }
    base.update(overrides)
    return base


class TestFeatureVector:
    def test_độ_dài_khớp_tên_đặc_trưng(self):
        assert len(extract_features(make_candidate())) == len(FEATURE_NAMES)

    def test_không_có_tên_trùng(self):
        assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)

    def test_rating_thiếu_thành_nan_chứ_không_phải_0(self):
        row = feature_row(make_candidate(rating=None))
        assert math.isnan(row["rating"])
        assert row["has_rating"] == 0.0

    def test_rating_0_cũng_coi_là_thiếu(self):
        """Dữ liệu chưa chạy migration 0008 vẫn còn rating = 0; nếu đưa thẳng
        vào mô hình thì 99% POI thật bị học như "bị chấm 0/5"."""
        row = feature_row(make_candidate(rating=0))
        assert math.isnan(row["rating"])
        assert row["has_rating"] == 0.0

    def test_rating_thật_được_giữ_nguyên(self):
        row = feature_row(make_candidate(rating=4.5))
        assert row["rating"] == pytest.approx(4.5)
        assert row["has_rating"] == 1.0

    def test_rating_seed_hoặc_google_bị_coi_là_thiếu(self):
        """Chỉ ratingSource='user' (đánh giá thật qua app/reviews.py) mới được
        tính là có dữ liệu — cùng lý do cột `source` bị loại khỏi feature set:
        cho model thấy rating gõ tay (seed) hay mua ngoài (google) thì nó học
        "nguồn X = tốt" thay vì học mức liên quan thật."""
        for source in ("seed", "google", None):
            row = feature_row(make_candidate(rating=4.9, reviewCount=500, ratingSource=source))
            assert math.isnan(row["rating"]), f"source={source} không được lọt qua"
            assert math.isnan(row["review_count"]), f"source={source} không được lọt qua"
            assert row["has_rating"] == 0.0

    def test_price_level_0_là_chưa_rõ(self):
        assert math.isnan(feature_row(make_candidate(priceLevel=0))["price_level"])
        assert feature_row(make_candidate(priceLevel=3))["price_level"] == 3.0

    def test_open_now_ba_trạng_thái(self):
        assert feature_row(make_candidate(openNow=True))["is_open"] == 1.0
        assert feature_row(make_candidate(openNow=False))["is_open"] == 0.0
        assert math.isnan(feature_row(make_candidate(openNow=None))["is_open"])

    def test_spatial_decay_giảm_theo_khoảng_cách(self):
        gần = feature_row(make_candidate(distanceMeters=100))["spatial_decay"]
        xa = feature_row(make_candidate(distanceMeters=5000))["spatial_decay"]
        assert gần > xa

    def test_in_graph_và_category_affinity(self):
        row = feature_row(
            make_candidate(),
            category_boost={"cafe": 0.15},
            graph_boost={"poi-1"},
        )
        assert row["in_graph"] == 1.0
        assert row["category_affinity"] == pytest.approx(0.15)

    def test_candidate_rỗng_không_ném_lỗi(self):
        """Ứng viên thiếu gần hết trường vẫn phải ra vector đúng độ dài —
        đường phục vụ không được gãy vì một POI thiếu dữ liệu."""
        row = extract_features({"id": "x", "category": None})
        assert len(row) == len(FEATURE_NAMES)

    def test_coverage_đếm_đúng_tỉ_lệ_thiếu(self):
        rows = [
            extract_features(make_candidate(rating=4.5)),
            extract_features(make_candidate(rating=None)),
        ]
        result = coverage(rows)
        assert result["rating"] == 0.5
        assert result["distance_meters"] == 1.0


class TestModelFallback:
    def test_không_có_mô_hình_thì_trả_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LTR_MODEL_DIR", str(tmp_path))
        ltr_model.reset_cache()
        assert ltr_model.load() is None
        assert ltr_model.available() is False
        assert ltr_model.score([make_candidate()]) is None

    def test_từ_chối_mô_hình_lệch_danh_sách_đặc_trưng(self, tmp_path, monkeypatch):
        """Đổi FEATURE_NAMES mà quên huấn luyện lại là lỗi im lặng nguy hiểm
        nhất: cột thứ i lúc phục vụ không còn là cột thứ i lúc huấn luyện, mô
        hình vẫn chạy và vẫn trả điểm — chỉ có điều điểm đó vô nghĩa."""
        (tmp_path / "model.txt").write_text("giả lập", encoding="utf-8")
        (tmp_path / "model.meta.json").write_text(
            json.dumps({"featureNames": ["chỉ_một_cột"]}), encoding="utf-8"
        )
        monkeypatch.setenv("LTR_MODEL_DIR", str(tmp_path))
        ltr_model.reset_cache()
        assert ltr_model.load() is None

    def test_meta_hỏng_thì_tắt_ltr_chứ_không_ném_lỗi(self, tmp_path, monkeypatch):
        (tmp_path / "model.txt").write_text("giả lập", encoding="utf-8")
        (tmp_path / "model.meta.json").write_text("{ không phải json", encoding="utf-8")
        monkeypatch.setenv("LTR_MODEL_DIR", str(tmp_path))
        ltr_model.reset_cache()
        assert ltr_model.load() is None


class TestRerankVớiDữLiệuThiếu:
    """`rerank` phải phân biệt "chưa có đánh giá" với "đánh giá kém"."""

    def _pair(self, rating_a, rating_b):
        candidates = [
            make_candidate(id="a", rating=rating_a),
            make_candidate(id="b", rating=rating_b),
        ]
        ranked = ranking.rerank(candidates)
        return {item["id"]: item["score"] for item in ranked}

    def test_thiếu_rating_không_bị_phạt_như_rating_0(self):
        thiếu = self._pair(None, 4.5)
        kém = self._pair(0.0, 4.5)
        # POI thiếu rating phải ăn điểm cao hơn POI thật sự bị chấm 0/5.
        assert thiếu["a"] > kém["a"]

    def test_hai_ứng_viên_cùng_thiếu_rating_vẫn_so_sánh_được(self):
        candidates = [
            make_candidate(id="gần", rating=None, distanceMeters=100),
            make_candidate(id="xa", rating=None, distanceMeters=9000),
        ]
        ranked = ranking.rerank(candidates)
        assert ranked[0]["id"] == "gần"

    def test_điểm_nằm_trong_khoảng_0_1(self):
        """Chuẩn hóa theo tổng trọng số áp dụng được nên điểm phải ở [0,1],
        thay vì [0, 1.18] như khi cộng thẳng chín số hạng."""
        scores = self._pair(None, 4.5)
        assert all(0.0 <= value <= 1.0 for value in scores.values())

    def test_ghi_nhãn_bộ_xếp_hạng_đã_dùng(self):
        ranked = ranking.rerank([make_candidate()])
        assert ranked[0]["rankerUsed"] == "linear"

    def test_xin_ltr_mà_thiếu_mô_hình_thì_rơi_về_linear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LTR_MODEL_DIR", str(tmp_path))
        ltr_model.reset_cache()
        ranked = ranking.rerank([make_candidate()], ranker="ltr")
        assert ranked[0]["rankerUsed"] == "linear"
        assert "score" in ranked[0]
