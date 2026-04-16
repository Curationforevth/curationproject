"""B3: dedup_checker 가 load/lookup 양쪽에 clean_title 을 일관 적용하는지 검증."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock

from scripts.lib.dedup_checker import DeduplicateChecker


def _fake_sb_with_rows(rows):
    sb = MagicMock()
    sb.table.return_value.select.return_value.range.return_value \
        .execute.return_value.data = rows
    return sb


def test_load_title_index_applies_clean_title():
    """DB raw title '채식주의자 (리커버)' 가 clean_title 을 통과 후
    '채식주의자' 로 인덱싱되어야 한다."""
    sb = _fake_sb_with_rows([
        {"isbn": "978X", "title": "채식주의자 (리커버)", "author": "한강"},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()

    # 새 책 (이미 clean 된 title) 이 같은 key 로 매칭되어야 함
    assert dc.is_title_duplicate("채식주의자", "한강", "978Y") is True


def test_same_isbn_is_not_duplicate():
    sb = _fake_sb_with_rows([
        {"isbn": "978X", "title": "채식주의자", "author": "한강"},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()

    # 같은 ISBN = 업데이트 대상, 중복 아님
    assert dc.is_title_duplicate("채식주의자", "한강", "978X") is False


def test_new_book_not_in_index():
    sb = _fake_sb_with_rows([])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()

    assert dc.is_title_duplicate("새로운 책", "저자", "999") is False


# ── Strategy C (2026-04-16) — DedupAction check() 테스트 ──

from scripts.lib.dedup_checker import DedupAction


def test_check_returns_new_when_not_in_index():
    sb = _fake_sb_with_rows([])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    action, book_id = dc.check("새 책", "저자", "999", loan_count=100)
    assert action == DedupAction.NEW
    assert book_id is None


def test_check_returns_new_for_same_isbn():
    sb = _fake_sb_with_rows([
        {"id": "bid-1", "isbn": "978X", "title": "채식주의자", "author": "한강", "loan_count": 5000},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    # 같은 ISBN 은 upsert 로 처리 → NEW (merge_richer 가 자체 loan_count 비교)
    action, book_id = dc.check("채식주의자", "한강", "978X", loan_count=6000)
    assert action == DedupAction.NEW


def test_check_returns_update_when_new_loan_count_higher():
    sb = _fake_sb_with_rows([
        {"id": "bid-1", "isbn": "978X", "title": "채식주의자", "author": "한강", "loan_count": 1000},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    # 다른 ISBN 의 같은 작품 — 새 loan_count 가 더 큼
    action, book_id = dc.check("채식주의자", "한강", "978Y", loan_count=5000)
    assert action == DedupAction.UPDATE_LOAN_COUNT
    assert book_id == "bid-1"


def test_check_returns_skip_when_new_loan_count_lower():
    sb = _fake_sb_with_rows([
        {"id": "bid-1", "isbn": "978X", "title": "채식주의자", "author": "한강", "loan_count": 5000},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    # 다른 ISBN 의 같은 작품 — 새 loan_count 가 더 작음 → 버림
    action, book_id = dc.check("채식주의자", "한강", "978Y", loan_count=1000)
    assert action == DedupAction.SKIP


def test_check_picks_highest_when_multiple_editions_exist():
    sb = _fake_sb_with_rows([
        {"id": "bid-a", "isbn": "978A", "title": "채식주의자", "author": "한강", "loan_count": 2000},
        {"id": "bid-b", "isbn": "978B", "title": "채식주의자", "author": "한강", "loan_count": 5000},
        {"id": "bid-c", "isbn": "978C", "title": "채식주의자", "author": "한강", "loan_count": 1000},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    # 새 loan_count 가 5000 보다 커야 UPDATE 가능, 그래야 bid-b 가 대상
    action, book_id = dc.check("채식주의자", "한강", "978D", loan_count=6000)
    assert action == DedupAction.UPDATE_LOAN_COUNT
    assert book_id == "bid-b"


def test_update_loan_count_mutates_index():
    sb = _fake_sb_with_rows([
        {"id": "bid-1", "isbn": "978X", "title": "채식주의자", "author": "한강", "loan_count": 1000},
    ])
    dc = DeduplicateChecker(sb)
    dc.load_title_index()
    dc.update_loan_count("bid-1", 8000)
    # 이제 새 ISBN 이 3000 이면 UPDATE 가 아니라 SKIP 이어야 함
    action, _ = dc.check("채식주의자", "한강", "978Y", loan_count=3000)
    assert action == DedupAction.SKIP
