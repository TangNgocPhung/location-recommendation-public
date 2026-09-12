from app.features import offline


def test_smoothed_ctr_uses_prior_so_one_click_is_not_perfect():
    # 1 click / 1 impression KHÔNG được ra CTR = 1.0
    assert offline.smoothed_ctr(1, 1) < 0.3
    # nhiều dữ liệu thì tiến gần tỉ lệ thật
    assert 0.45 < offline.smoothed_ctr(500, 1000) < 0.55
    # không có dữ liệu -> quanh CTR nền 0.1
    assert offline.smoothed_ctr(0, 0) == 0.1


def test_smoothed_ctr_monotonic_in_clicks():
    assert offline.smoothed_ctr(10, 100) > offline.smoothed_ctr(5, 100)


def test_compute_user_profiles_assembles_affinity(monkeypatch):
    rows = [
        {"session_id": "s1", "category": "cafe", "weight": 8.0, "events": 4, "avg_price": 2.0},
        {"session_id": "s1", "category": "park", "weight": 2.0, "events": 1, "avg_price": None},
        {"session_id": "s2", "category": "bar", "weight": 3.0, "events": 3, "avg_price": 3.0},
    ]
    monkeypatch.setattr(offline, "_fetch", lambda url, query: rows)

    profiles = {p["session_id"]: p for p in offline.compute_user_profiles("x")}

    s1 = profiles["s1"]
    assert s1["event_count"] == 5
    assert s1["top_category"] == "cafe"
    assert s1["affinity"]["cafe"] == 1.0  # chuẩn hóa theo max
    assert s1["affinity"]["park"] == 0.25
    assert s1["pref_price_level"] == 2.0
    assert profiles["s2"]["top_category"] == "bar"


def test_compute_region_ctr_builds_region_key(monkeypatch):
    rows = [{"district": "Quận 1", "category": "cafe", "impressions": 100, "clicks": 20}]
    monkeypatch.setattr(offline, "_fetch", lambda url, query: rows)

    out = offline.compute_region_ctr("x")[0]

    assert out["region"] == "Quận 1|cafe"
    assert out["clicks"] == 20 and out["impressions"] == 100
    assert 0.15 < out["ctr"] < 0.25
