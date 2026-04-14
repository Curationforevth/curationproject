import numpy as np
import pytest
from engine.twostage import stage1_hybrid


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def stage1_data():
    bids = ["novel1", "novel2", "econ1", "econ2", "sci1"]
    descs = np.stack([
        _norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
        _norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
        _norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
        _norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
        _norm([0, 0, 1, 0, 0, 0, 0, 0.5]),
    ]).astype(np.float16)
    agg_reasons = np.stack([
        _norm([1, 0, 0, 0, 0.6, 0.3, 0, 0]),
        _norm([1, 0, 0, 0, 0, 0.9, 0, 0]),
        _norm([0, 1, 0, 0, 0, 0, 0.6, 0.3]),
        _norm([0, 1, 0, 0, 0, 0, 0.9, 0]),
        _norm([0, 0, 1, 0, 0, 0, 0, 0.8]),
    ]).astype(np.float16)
    return descs, agg_reasons, bids


class TestStage1Hybrid:
    def test_novel_fan_gets_novels_first(self, stage1_data):
        desc_mat, agg_mat, bid_order = stage1_data
        liked = {"novel1": {"rating": "good"}}
        candidates = stage1_hybrid(liked, {}, desc_mat, agg_mat, bid_order, top_n=3)
        assert "novel2" in candidates
        assert "novel1" not in candidates

    def test_excludes_read_books(self, stage1_data):
        desc_mat, agg_mat, bid_order = stage1_data
        liked = {"novel1": {"rating": "good"}, "novel2": {"rating": "bad"}}
        candidates = stage1_hybrid(liked, {}, desc_mat, agg_mat, bid_order, top_n=5)
        assert "novel1" not in candidates
        assert "novel2" not in candidates

    def test_fb_data_influences_ranking(self, stage1_data):
        desc_mat, agg_mat, bid_order = stage1_data
        liked = {"econ1": {"rating": "good"}}
        fb = {"econ1": {"emb": _norm([0, 1, 0, 0, 0, 0, 0.7, 0]).astype(np.float32),
                        "is_dislike": False}}
        candidates = stage1_hybrid(liked, fb, desc_mat, agg_mat, bid_order, top_n=3)
        assert candidates[0] == "econ2"

    def test_returns_at_most_top_n(self, stage1_data):
        desc_mat, agg_mat, bid_order = stage1_data
        liked = {"novel1": {"rating": "good"}}
        candidates = stage1_hybrid(liked, {}, desc_mat, agg_mat, bid_order, top_n=2)
        assert len(candidates) == 2

    def test_empty_liked_returns_empty(self, stage1_data):
        desc_mat, agg_mat, bid_order = stage1_data
        candidates = stage1_hybrid({}, {}, desc_mat, agg_mat, bid_order, top_n=3)
        assert candidates == []
