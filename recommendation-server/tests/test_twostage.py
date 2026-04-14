import numpy as np
import pytest
from engine.twostage import stage1_hybrid, batch_score_prestacked
from engine.scorer import _score_one


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


# ---------------------------------------------------------------------------
# TestBatchScorePrestacked
# ---------------------------------------------------------------------------

def _make_prestacked(index):
    """small_index의 reason 리스트를 float16 prestacked dict으로 변환."""
    result = {}
    for bid in index.book_ids:
        bv = index.get_book(bid)
        if bv and bv.reasons:
            result[bid] = np.stack(bv.reasons).astype(np.float16)
        else:
            result[bid] = np.zeros((1, index.dim), dtype=np.float16)
    return result


class TestBatchScorePrestacked:

    def test_matches_original_scorer(self, small_index):
        """batch_score_prestacked 결과가 _score_one과 동일해야 한다 (max diff < 0.01)."""
        liked = {
            "novel1": {"rating": "good"},
            "econ1": {"rating": "bad"},
        }
        fb_data = {
            "novel1": {
                "emb": _norm([1, 0, 0, 0, 0.7, 0.3, 0, 0]).astype(np.float32),
                "is_dislike": False,
            }
        }
        candidate_ids = [bid for bid in small_index.book_ids if bid not in liked]
        prestacked = _make_prestacked(small_index)

        batch_scores = batch_score_prestacked(
            small_index, liked, fb_data, candidate_ids, prestacked
        )
        original_scores = {
            cid: _score_one(small_index, liked, fb_data, cid)
            for cid in candidate_ids
        }

        assert set(batch_scores.keys()) == set(original_scores.keys())
        for cid in candidate_ids:
            diff = abs(batch_scores[cid] - original_scores[cid])
            assert diff < 0.01, (
                f"{cid}: batch={batch_scores[cid]:.6f} original={original_scores[cid]:.6f} diff={diff:.6f}"
            )

    def test_excludes_missing_books(self, small_index):
        """인덱스에 없는 candidate는 결과에서 제외된다."""
        liked = {"novel1": {"rating": "good"}}
        prestacked = _make_prestacked(small_index)
        candidate_ids = ["novel2", "NONEXISTENT_BOOK"]

        scores = batch_score_prestacked(
            small_index, liked, {}, candidate_ids, prestacked
        )

        assert "novel2" in scores
        assert "NONEXISTENT_BOOK" not in scores

    def test_handles_bad_ratings(self, small_index):
        """bad 평점 책과 유사한 후보의 점수는 good과 유사한 후보보다 낮아야 한다."""
        liked = {
            "novel1": {"rating": "good"},
            "econ1": {"rating": "bad"},
        }
        prestacked = _make_prestacked(small_index)
        candidate_ids = ["novel2", "econ2", "sci1"]

        scores = batch_score_prestacked(
            small_index, liked, {}, candidate_ids, prestacked
        )

        # novel과 유사한 novel2가 econ과 유사한 econ2보다 높아야 함
        assert scores["novel2"] > scores["econ2"]
