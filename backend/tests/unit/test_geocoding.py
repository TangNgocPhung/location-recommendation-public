from app.geocoding import normalize_text, split_subject_and_location


def test_normalize_text_preserves_vietnamese_and_collapses_whitespace() -> None:
    assert normalize_text("  Cà   PHÊ  ") == "cà phê"


def test_split_subject_and_location_understands_vietnamese_connector() -> None:
    assert split_subject_and_location("Cà phê gần Bến Thành") == (
        "cà phê",
        "bến thành",
    )


def test_split_subject_and_location_keeps_plain_query() -> None:
    assert split_subject_and_location("bảo tàng") == ("bảo tàng", None)
