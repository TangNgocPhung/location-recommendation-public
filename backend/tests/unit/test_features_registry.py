from app.features.registry import (
    POI_EMBEDDING,
    REGION_CTR,
    REGISTRY,
    USER_PROFILE,
    get_view,
    region_key,
)


def test_online_key_carries_version_and_view_name():
    key = USER_PROFILE.online_key("sess-1")
    assert key == "feat:v1:user_profile:sess-1"
    assert USER_PROFILE.version in key  # version nằm trong khóa -> đổi version không đụng dữ liệu cũ


def test_registry_covers_the_three_features_in_the_diagram():
    assert set(REGISTRY) == {"user_profile", "region_ctr", "poi_embedding"}
    assert USER_PROFILE.entity == "session_id"
    assert REGION_CTR.entity == "region"
    assert POI_EMBEDDING.entity == "poi_id"


def test_poi_embedding_version_matches_ingest_model():
    # phải trùng EMBEDDING_MODEL dùng khi ingest để training/serving không lệch
    from app.poi_features import EMBEDDING_MODEL

    assert POI_EMBEDDING.version == EMBEDDING_MODEL


def test_region_key_normalizes_missing_parts():
    assert region_key("Quận 1", "cafe") == "Quận 1|cafe"
    assert region_key(None, "cafe") == "unknown|cafe"
    assert region_key("Quận 1", None) == "Quận 1|unknown"


def test_get_view_returns_registered_view():
    assert get_view("region_ctr") is REGION_CTR
