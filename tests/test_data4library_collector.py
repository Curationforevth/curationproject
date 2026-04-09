"""data4library_collector 하드닝 테스트.

목적: hard import + 순수 파서 + run() exit code.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch


def test_hard_import_no_silent_fallback():
    import data4library_collector
    from lib.retry import with_retry as real_retry
    assert data4library_collector.with_retry is real_retry


def test_parse_keywords_extracts_words():
    from data4library_collector import parse_keywords
    response = {"response": {"keywords": [
        {"keyword": {"word": "사랑"}},
        {"keyword": {"word": "성장"}},
        {"keyword": {}},  # 빈 keyword 는 스킵
    ]}}
    assert parse_keywords(response) == ["사랑", "성장"]


def test_parse_keywords_empty_or_malformed():
    from data4library_collector import parse_keywords
    assert parse_keywords(None) == []
    assert parse_keywords({}) == []
    # response.keywords 가 list 가 아닌 경우는 try/except 로 [] 반환
    assert parse_keywords({"response": {"keywords": None}}) == []


def test_parse_co_loan_books_caps_at_50():
    from data4library_collector import parse_co_loan_books, CO_LOAN_CAP
    response = {"response": {"coLoanBooks": [
        {"book": {"isbn13": f"978{i:010d}"}}
        for i in range(100)
    ]}}
    result = parse_co_loan_books(response)
    assert len(result) == CO_LOAN_CAP


def test_parse_co_loan_books_skips_missing_isbn():
    from data4library_collector import parse_co_loan_books
    response = {"response": {"coLoanBooks": [
        {"book": {"isbn13": "9781234567890"}},
        {"book": {}},  # 스킵
        {"book": {"isbn13": "9789876543210"}},
    ]}}
    assert parse_co_loan_books(response) == ["9781234567890", "9789876543210"]


def test_run_returns_zero_when_no_books():
    import data4library_collector
    with patch.object(data4library_collector, "create_client", return_value=MagicMock()):
        c = data4library_collector.Data4LibraryCollector(dry_run=True)
        with patch.object(c, "fetch_books_needing_collection", return_value=[]):
            rc = c.run(limit=10)
    assert rc == 0


def test_run_returns_one_on_errors():
    import data4library_collector
    with patch.object(data4library_collector, "create_client", return_value=MagicMock()):
        c = data4library_collector.Data4LibraryCollector(dry_run=True)
        books = [{"id": "b1", "isbn": "9781234567890"}]
        with patch.object(c, "fetch_books_needing_collection", return_value=books):
            with patch.object(c, "fetch_usage", side_effect=RuntimeError("api down")):
                with patch("time.sleep"):
                    rc = c.run(limit=1)
    assert rc == 1
    assert c.stats["errors"] == 1


def test_run_returns_zero_on_success():
    import data4library_collector
    with patch.object(data4library_collector, "create_client", return_value=MagicMock()):
        c = data4library_collector.Data4LibraryCollector(dry_run=True)
        books = [{"id": "b1", "isbn": "9781234567890"}]
        with patch.object(c, "fetch_books_needing_collection", return_value=books):
            with patch.object(c, "fetch_usage", return_value=(["사랑"], ["9789999999999"])):
                with patch("time.sleep"):
                    rc = c.run(limit=1)
    assert rc == 0
    assert c.stats["processed"] == 1
    assert c.stats["keywords_found"] == 1
    assert c.stats["co_loan_found"] == 1
