from lib.book_filter import is_non_book


def test_filters_toeic_book():
    item = {"title": "토익 실전 1000제", "categoryName": "외국어"}
    assert is_non_book(item) is True


def test_filters_exam_category():
    item = {"title": "행정법 총론", "categoryName": "취업/수험서"}
    assert is_non_book(item) is True


def test_passes_novel():
    item = {"title": "살인자의 기억법", "categoryName": "소설/시/희곡"}
    assert is_non_book(item) is False


def test_passes_essay():
    item = {"title": "나는 나로 살기로 했다", "categoryName": "에세이"}
    assert is_non_book(item) is False


def test_handles_empty_fields():
    item = {"title": "", "categoryName": ""}
    assert is_non_book(item) is False


def test_handles_none_fields():
    item = {"title": None, "categoryName": None}
    assert is_non_book(item) is False
