from app.search import query as q


def test_bm25_body_has_fuzzy_geo_and_category_filters() -> None:
    body = q.bm25_body("ca phe", 10.77, 106.70, 3000, "cafe", 50)

    should = body["query"]["bool"]["must"][0]["bool"]["should"]
    folded_match = should[0]["multi_match"]
    assert folded_match["fuzziness"] == "AUTO:5,8"
    assert "name^3" in folded_match["fields"]

    filters = body["query"]["bool"]["filter"]
    assert {"term": {"category_label.raw": "cafe"}} in filters
    geo = [f for f in filters if "geo_distance" in f][0]["geo_distance"]
    assert geo["distance"] == "3000m"
    assert geo["location"] == {"lat": 10.77, "lon": 106.70}


def test_bm25_body_strict_field_has_no_fuzziness() -> None:
    """``.strict`` (giữ dấu thanh điệu) KHÔNG được fuzzy — nếu fuzzy thì "viện"
    và "viên" (khác đúng 1 ký tự) sẽ lại khớp mờ, xoá tác dụng phân biệt dấu
    thanh mà field này tồn tại để giải quyết (xem docstring `bm25_body`)."""
    body = q.bm25_body("bệnh viện", 10.77, 106.70, 3000, None, 50)

    should = body["query"]["bool"]["must"][0]["bool"]["should"]
    strict_match = should[1]["multi_match"]
    assert "fuzziness" not in strict_match
    assert "name.strict^5" in strict_match["fields"]


def test_bm25_body_without_category_has_only_geo_filter() -> None:
    body = q.bm25_body("pho", 10.0, 106.0, 1000, None, 10)
    filters = body["query"]["bool"]["filter"]

    assert all("term" not in f for f in filters)
    assert len(filters) == 1


def test_geo_body_sorts_by_distance_and_has_no_text_clause() -> None:
    body = q.geo_body(10.0, 106.0, 2000, None, 25)

    assert "must" not in body["query"]["bool"]
    assert body["sort"][0]["_geo_distance"]["order"] == "asc"
    assert body["size"] == 25


def test_vector_body_carries_embedding_and_filter() -> None:
    embedding = [0.1] * 64
    body = q.vector_body(embedding, 10.0, 106.0, 2000, "cafe", 30)

    knn = body["query"]["knn"]["embedding"]
    assert knn["vector"] == embedding
    assert knn["k"] == 30
    inner = knn["filter"]["bool"]["filter"]
    assert {"term": {"category_label.raw": "cafe"}} in inner


def test_extract_ranked_ids_prefers_source_then_id() -> None:
    response = {
        "hits": {
            "hits": [
                {"_id": "ignored", "_source": {"poi_id": "p1"}},
                {"_id": "p2", "_source": {}},
            ]
        }
    }

    assert q.extract_ranked_ids(response) == ["p1", "p2"]


def test_extract_ranked_ids_handles_empty() -> None:
    assert q.extract_ranked_ids({}) == []
    assert q.extract_ranked_ids({"hits": {"hits": []}}) == []


def test_bm25_fuzzy_khong_ap_len_am_tiet_ngan() -> None:
    """Âm tiết <= 4 ký tự không được khớp mờ: với "AUTO" (=3,6), "benh" sửa 1 ký
    tự thành "ben"/"binh" và "bệnh viện" trả Công viên Bến Bạch Đằng / Công
    viên Lãnh Binh Thăng (đo được 19/09/2026)."""
    body = q.bm25_body("bệnh viện", 10.77, 106.70, 3000, None, 50)

    folded_match = body["query"]["bool"]["must"][0]["bool"]["should"][0]["multi_match"]
    low, high = folded_match["fuzziness"].removeprefix("AUTO:").split(",")
    assert int(low) >= 5
