"""Discovery collector — pure logic 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.data4library_discovery_collector import (
    KDC_BUCKETS,
    dedup_in_batch_by_isbn,
    sanitize_for_upsert,
    extract_first_author,
)


def test_kdc_buckets_cover_main_genres():
    keys = {b["kdc"] for b in KDC_BUCKETS}
    assert "8" in keys  # 문학
    assert "1" in keys  # 철학
    assert "3" in keys  # 사회과학
    assert "9" in keys  # 역사


def test_dedup_in_batch_by_isbn_keeps_highest_loan_count():
    rows = [
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 100},
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 250},
        {"isbn13": "9788954682152", "title": "작별하지 않는다", "loan_count": 200},
    ]
    out = dedup_in_batch_by_isbn(rows)
    assert len(out) == 2
    by_isbn = {r["isbn13"]: r for r in out}
    assert by_isbn["9788936434120"]["loan_count"] == 250
    assert by_isbn["9788954682152"]["loan_count"] == 200


def test_extract_first_author_strips_role_prefix():
    assert extract_first_author("지은이: 한강") == "한강"
    assert extract_first_author("저자: 유발 하라리 ;옮긴이: 조현욱") == "유발 하라리"
    assert extract_first_author("글: 최설희 ;그림: 한현동") == "최설희"
    assert extract_first_author("한강") == "한강"
    assert extract_first_author("") == ""
    assert extract_first_author(None) == ""


def test_sanitize_for_upsert_maps_columns():
    parsed = {
        "isbn13": "9788936434120",
        "title": "소년이 온다 :한강 장편소설",
        "author_raw": "지은이: 한강",
        "publisher": "창비",
        "publication_year": "2014",
        "addition_symbol": "03810",
        "kdc": "813.62",
        "cover_url": "http://example.com/cover.jpg",
        "loan_count": 3699,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9788936434120"
    assert row["title"] == "소년이 온다 :한강 장편소설"
    assert row["author"] == "한강"
    assert row["publisher"] == "창비"
    assert row["cover_url"] == "http://example.com/cover.jpg"
    assert row["loan_count"] == 3699
    assert row["sales_point"] == 3699
    assert "isbn13" not in row
    assert "kdc" not in row
    assert "addition_symbol" not in row
    assert "publication_year" not in row
    assert "author_raw" not in row


def test_sanitize_for_upsert_handles_missing_optional():
    parsed = {
        "isbn13": "9999999999999",
        "title": "x",
        "author_raw": "",
        "publisher": None,
        "publication_year": None,
        "addition_symbol": "",
        "kdc": None,
        "cover_url": None,
        "loan_count": 0,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9999999999999"
    assert row["author"] == ""
    assert row["loan_count"] == 0
