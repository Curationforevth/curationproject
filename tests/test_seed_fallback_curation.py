"""Fallback curation 시드 스크립트 — pure logic 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.seed_fallback_curation import (
    rank_books_by_loan_count,
    build_fallback_rows,
)


def test_rank_books_by_loan_count_descending():
    books = [
        {"id": "u1", "loan_count": 100},
        {"id": "u2", "loan_count": 500},
        {"id": "u3", "loan_count": 250},
    ]
    out = rank_books_by_loan_count(books)
    assert [b["id"] for b in out] == ["u2", "u3", "u1"]


def test_rank_books_by_loan_count_skips_null():
    books = [
        {"id": "u1", "loan_count": None},
        {"id": "u2", "loan_count": 100},
    ]
    out = rank_books_by_loan_count(books)
    assert len(out) == 1
    assert out[0]["id"] == "u2"


def test_build_fallback_rows_assigns_sequential_ranks():
    ranked = [
        {"id": "u2", "loan_count": 500},
        {"id": "u3", "loan_count": 250},
        {"id": "u1", "loan_count": 100},
    ]
    rows = build_fallback_rows(ranked)
    assert rows == [
        {"rank": 1, "book_id": "u2", "loan_count": 500},
        {"rank": 2, "book_id": "u3", "loan_count": 250},
        {"rank": 3, "book_id": "u1", "loan_count": 100},
    ]


def test_build_fallback_rows_truncates_to_limit():
    ranked = [{"id": f"u{i}", "loan_count": 100 - i} for i in range(50)]
    rows = build_fallback_rows(ranked, limit=30)
    assert len(rows) == 30
    assert rows[0]["rank"] == 1
    assert rows[-1]["rank"] == 30
