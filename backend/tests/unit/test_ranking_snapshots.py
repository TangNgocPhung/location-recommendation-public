"""Kiểm thử phần thuần logic của ranking snapshot (Phase 5.5).

Phần chạm Postgres được kiểm bằng cách gọi API thật (xem session note); ở đây
chỉ cái sai được mà KHÔNG có lỗi rõ ràng nào: `feature_row()` dùng NaN cho
giá trị thiếu, nhưng `json.dumps(float('nan'))` in ra token `NaN` — hợp lệ với
Python, không hợp lệ với JSON — nên Postgres JSONB từ chối insert với lỗi
"invalid input syntax for type json" ngay cả khi code Python không ném lỗi gì.
"""

from __future__ import annotations

import json
import math

from app.ranking_snapshots import _json_safe_features


def test_nan_thanh_null_de_json_dumps_khong_sinh_token_nan_khong_hop_le():
    features = {"rating": float("nan"), "distance_meters": 120.5, "has_rating": 0.0}
    safe = _json_safe_features(features)

    assert safe["rating"] is None
    assert safe["distance_meters"] == 120.5
    assert safe["has_rating"] == 0.0

    # Chính điều kiện gây lỗi thật: json.dumps(NaN) không ném lỗi ở phía Python
    # nhưng in ra "NaN" mà Postgres JSONB từ chối. Sau khi lọc, chuỗi JSON phải
    # sạch, không còn token NaN nào.
    encoded = json.dumps(safe)
    assert "NaN" not in encoded
    assert json.loads(encoded)["rating"] is None


def test_khong_dung_den_gia_tri_khong_phai_nan():
    """0.0 hợp lệ (có dữ liệu, bằng không) — không được lẫn với NaN (thiếu)."""
    safe = _json_safe_features({"popularity_score": 0.0})
    assert safe["popularity_score"] == 0.0
    assert not math.isnan(0.0)
