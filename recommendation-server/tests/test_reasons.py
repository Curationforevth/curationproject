"""engine.reasons.pick_top_reasons — 책별 대표 reason 선택(순수 함수)."""
from engine.reasons import pick_top_reasons


def test_picks_highest_user_mention_count():
    rows = [
        {"book_id": "b1", "reason": "잔잔한 문체", "user_mention_count": 1},
        {"book_id": "b1", "reason": "몰입되는 세계관", "user_mention_count": 5},
        {"book_id": "b1", "reason": "빠른 전개", "user_mention_count": 2},
    ]
    assert pick_top_reasons(rows) == {"b1": "몰입되는 세계관"}


def test_multiple_books():
    rows = [
        {"book_id": "b1", "reason": "A", "user_mention_count": 0},
        {"book_id": "b2", "reason": "B", "user_mention_count": 0},
    ]
    assert pick_top_reasons(rows) == {"b1": "A", "b2": "B"}


def test_skips_empty_and_null_reason():
    rows = [
        {"book_id": "b1", "reason": "  ", "user_mention_count": 3},
        {"book_id": "b1", "reason": None, "user_mention_count": 9},
        {"book_id": "b1", "reason": "유효", "user_mention_count": 1},
    ]
    assert pick_top_reasons(rows) == {"b1": "유효"}


def test_missing_mention_count_treated_as_zero():
    rows = [
        {"book_id": "b1", "reason": "첫 이유"},
        {"book_id": "b1", "reason": "둘째", "user_mention_count": 0},
    ]
    # 동점(0) → 처음 것 유지
    assert pick_top_reasons(rows) == {"b1": "첫 이유"}


def test_empty_rows():
    assert pick_top_reasons([]) == {}
