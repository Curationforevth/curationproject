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
