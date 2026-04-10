"""정보나루 4개 endpoint wrapper 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.lib.data4library_api import (
    parse_book_docs,
    parse_monthly_keywords,
    is_adult_general,
    build_loan_item_params,
    build_recommand_params,
    build_search_params,
    build_monthly_keywords_params,
)


# ----- parse_book_docs : dual-key (doc / book) -----

def test_parse_book_docs_handles_loan_item_format():
    response = {
        "response": {
            "docs": [
                {"doc": {
                    "no": 1, "ranking": "1",
                    "bookname": "소년이 온다 :한강 장편소설 ",
                    "authors": "지은이: 한강",
                    "publisher": "창비",
                    "publication_year": "2014",
                    "isbn13": "9788936434120",
                    "addition_symbol": "03810",
                    "class_no": "813.62",
                    "bookImageURL": "http://image.aladin.co.kr/cover.jpg",
                    "loan_count": "3699",
                }},
            ]
        }
    }
    books = parse_book_docs(response)
    assert len(books) == 1
    b = books[0]
    assert b["isbn13"] == "9788936434120"
    assert b["title"] == "소년이 온다 :한강 장편소설"
    assert b["author_raw"] == "지은이: 한강"
    assert b["publisher"] == "창비"
    assert b["addition_symbol"] == "03810"
    assert b["loan_count"] == 3699
    assert b["cover_url"] == "http://image.aladin.co.kr/cover.jpg"


def test_parse_book_docs_handles_recommand_format():
    response = {
        "response": {
            "docs": [
                {"book": {
                    "no": 1,
                    "bookname": "채식주의자:한강 연작소설",
                    "authors": "한강",
                    "publisher": "창비",
                    "isbn13": "9788936433598",
                    "addition_symbol": "",
                    "class_no": "813.6",
                    "bookImageURL": "https://example.com/cover.jpg",
                }},
            ]
        }
    }
    books = parse_book_docs(response)
    assert len(books) == 1
    b = books[0]
    assert b["isbn13"] == "9788936433598"
    assert b["title"] == "채식주의자:한강 연작소설"
    assert b["loan_count"] == 0


def test_parse_book_docs_skips_books_without_isbn():
    response = {"response": {"docs": [
        {"doc": {"bookname": "no isbn"}},
        {"doc": {"bookname": "ok", "isbn13": "1111111111111"}},
    ]}}
    books = parse_book_docs(response)
    assert len(books) == 1
    assert books[0]["isbn13"] == "1111111111111"


def test_parse_book_docs_handles_empty_response():
    assert parse_book_docs({}) == []
    assert parse_book_docs({"response": {}}) == []
    assert parse_book_docs({"response": {"docs": []}}) == []


# ----- is_adult_general filter -----

def test_is_adult_general_accepts_first_digit_zero():
    assert is_adult_general({"addition_symbol": "03810"}) is True
    assert is_adult_general({"addition_symbol": "01000"}) is True


def test_is_adult_general_rejects_children_and_youth():
    assert is_adult_general({"addition_symbol": "73810"}) is False
    assert is_adult_general({"addition_symbol": "53810"}) is False
    assert is_adult_general({"addition_symbol": "83810"}) is False


def test_is_adult_general_treats_missing_as_pass():
    """빈 addition_symbol 은 pass (recommandList 에서 자주 빈 값 반환)."""
    assert is_adult_general({"addition_symbol": ""}) is True
    assert is_adult_general({}) is True


# ----- monthly keywords parsing -----

def test_parse_monthly_keywords_extracts_words_with_weight():
    response = {
        "response": {
            "keywords": [
                {"keyword": {"word": "사랑", "weight": "48.354"}},
                {"keyword": {"word": "나태주", "weight": "25.328"}},
            ]
        }
    }
    kws = parse_monthly_keywords(response)
    assert len(kws) == 2
    assert kws[0] == ("사랑", 48.354)
    assert kws[1] == ("나태주", 25.328)


def test_parse_monthly_keywords_handles_missing_weight():
    response = {"response": {"keywords": [{"keyword": {"word": "test"}}]}}
    kws = parse_monthly_keywords(response)
    assert len(kws) == 1
    assert kws[0] == ("test", 0.0)


def test_parse_monthly_keywords_empty():
    assert parse_monthly_keywords({}) == []
    assert parse_monthly_keywords({"response": {}}) == []


# ----- param builders -----

def test_build_loan_item_params_with_kdc():
    p = build_loan_item_params(
        api_key="abc", page_no=1, page_size=50,
        start_dt="2026-01-01", end_dt="2026-04-01", kdc="8",
    )
    assert p["authKey"] == "abc"
    assert p["format"] == "json"
    assert p["pageNo"] == 1
    assert p["pageSize"] == 50
    assert p["startDt"] == "2026-01-01"
    assert p["endDt"] == "2026-04-01"
    assert p["kdc"] == "8"


def test_build_loan_item_params_without_kdc():
    p = build_loan_item_params(
        api_key="abc", page_no=1, page_size=50,
        start_dt="2026-01-01", end_dt="2026-04-01",
    )
    assert "kdc" not in p


def test_build_recommand_params_requires_isbn13():
    p = build_recommand_params(api_key="abc", isbn13="9788936434120", page_size=10)
    assert p["authKey"] == "abc"
    assert p["isbn13"] == "9788936434120"
    assert p["pageSize"] == 10
    assert p["format"] == "json"


def test_build_search_params():
    p = build_search_params(api_key="abc", keyword="한강", page_no=1, page_size=10)
    assert p["authKey"] == "abc"
    assert p["keyword"] == "한강"
    assert p["pageNo"] == 1
    assert p["pageSize"] == 10


def test_build_monthly_keywords_params():
    p = build_monthly_keywords_params(api_key="abc", month="2026-03")
    assert p["authKey"] == "abc"
    assert p["month"] == "2026-03"
    assert p["format"] == "json"
