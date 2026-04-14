"""
test_cache.py — compute_input_hash 단위 테스트
Supabase 의존 함수(load_cache, save_cache_if_current, recompute_recommendations)는
통합 테스트이므로 이 파일에서 제외한다.
"""
from __future__ import annotations

import pytest
from engine.cache import compute_input_hash


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

def _make_book(book_id: str, rating: str, has_feedback: bool = False) -> dict:
    return {"book_id": book_id, "rating": rating, "feedback_embedding": "x" if has_feedback else None}


class TestComputeInputHash:
    def test_same_input_same_hash(self):
        data = [_make_book("A", "good"), _make_book("B", "neutral")]
        assert compute_input_hash(data) == compute_input_hash(data)

    def test_order_independent(self):
        data1 = [_make_book("A", "good"), _make_book("B", "bad")]
        data2 = [_make_book("B", "bad"), _make_book("A", "good")]
        assert compute_input_hash(data1) == compute_input_hash(data2)

    def test_different_ratings_different_hash(self):
        data_good = [_make_book("A", "good")]
        data_bad = [_make_book("A", "bad")]
        assert compute_input_hash(data_good) != compute_input_hash(data_bad)

    def test_feedback_changes_hash(self):
        without_fb = [_make_book("A", "good", has_feedback=False)]
        with_fb = [_make_book("A", "good", has_feedback=True)]
        assert compute_input_hash(without_fb) != compute_input_hash(with_fb)

    def test_empty_data_returns_valid_hex(self):
        result = compute_input_hash([])
        assert len(result) == 64
        # valid hex chars only
        int(result, 16)

    def test_returns_64_char_hex(self):
        data = [_make_book("X", "neutral")]
        result = compute_input_hash(data)
        assert len(result) == 64
        int(result, 16)
