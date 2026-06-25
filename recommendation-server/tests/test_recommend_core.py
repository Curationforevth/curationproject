import numpy as np
import pytest
from engine.recommend_core import compute_scored_books

class _FakeIndex:
    def __init__(self):
        self.book_ids = ["b1", "b2", "b3"]

def test_compute_scored_books_empty_inputs_returns_empty():
    idx = _FakeIndex()
    result = compute_scored_books(
        index=idx,
        liked_books={},
        fb_data={},
        prestacked_reasons=None,
        desc_matrix_f16=None,
        agg_reason_matrix_f16=None,
        bid_order=[],
    )
    assert result == []

def test_compute_scored_books_v3_fallback_used_when_prestacked_none(monkeypatch):
    idx = _FakeIndex()
    called = {}
    def fake_scores(index, liked_books, fb_data, top_n):
        called["v3"] = True
        return {"b1": 0.5, "b2": 0.3}
    monkeypatch.setattr("engine.recommend_core.recommend_scores_two_stage", fake_scores)

    result = compute_scored_books(
        index=idx,
        liked_books={"b1": {"rating": "good"}},
        fb_data={},
        prestacked_reasons=None,
        desc_matrix_f16=None,
        agg_reason_matrix_f16=None,
        bid_order=["b1", "b2"],
    )
    assert called.get("v3") is True
    assert isinstance(result, list)
