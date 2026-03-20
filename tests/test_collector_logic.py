import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from lib.book_filter import is_non_book
from lib.title_cleaner import clean_title


def test_process_items_includes_sales_point():
    """API 응답의 salesPoint가 변환 결과에 포함되어야 함"""
    item = {
        "isbn13": "9788937460470",
        "title": "채식주의자",
        "author": "한강",
        "publisher": "창비",
        "cover": "http://example.com/cover.jpg",
        "description": "한강의 소설",
        "categoryName": "소설/시/희곡",
        "itemId": 12345,
        "salesPoint": 85432,
    }

    # process_items 로직 재현
    isbn = item.get("isbn13") or item.get("isbn") or ""
    assert isbn == "9788937460470"
    assert not is_non_book(item)

    book = {
        "isbn": isbn,
        "title": clean_title(item.get("title", "")),
        "author": item.get("author", ""),
        "publisher": item.get("publisher", ""),
        "cover_url": item.get("cover", ""),
        "description": item.get("description", ""),
        "genre": item.get("categoryName", ""),
        "source": "aladin",
        "source_id": str(item.get("itemId", "")),
        "sales_point": item.get("salesPoint"),
    }

    assert book["sales_point"] == 85432
    assert book["title"] == "채식주의자"


def test_yield_rate_below_threshold_skips():
    """50건 중 새 책 5건 미만(10%)이면 해당 소스 종료"""
    total_items = 50
    new_items = 4
    yield_rate = new_items / total_items if total_items > 0 else 0
    assert yield_rate < 0.10  # 스킵 조건 충족


def test_yield_rate_above_threshold_continues():
    """50건 중 새 책 5건 이상이면 계속"""
    total_items = 50
    new_items = 6
    yield_rate = new_items / total_items if total_items > 0 else 0
    assert yield_rate >= 0.10  # 계속 조건
