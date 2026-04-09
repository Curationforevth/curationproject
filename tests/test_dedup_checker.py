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
