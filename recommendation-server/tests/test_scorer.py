import numpy as np
from engine.scorer import recommend_scores


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestScorer:
    def test_novel_fan_gets_novels(self, small_index):
        liked = {"novel1": {"rating": "good"}, "novel2": {"rating": "good"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        assert "novel1" not in scores
        assert "novel2" not in scores
        assert len(scores) == 3

    def test_dislike_pushes_away(self, small_index):
        liked = {"novel1": {"rating": "good"}, "econ1": {"rating": "bad"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        assert scores.get("econ2", 0) < scores.get("novel2", 0)

    def test_feedback_boosts_genre(self, small_index):
        liked = {"econ1": {"rating": "good"}, "econ2": {"rating": "good"},
                 "novel1": {"rating": "good"}}
        fb_data = {"novel1": {"emb": _norm([1, 0, 0, 0, 0.7, 0.3, 0, 0]),
                              "is_dislike": False}}
        scores = recommend_scores(small_index, liked, fb_data=fb_data)
        assert scores.get("novel2", 0) > scores.get("sci1", 0)

    def test_neutral_excluded(self, small_index):
        liked = {"novel1": {"rating": "good"}, "econ1": {"rating": "neutral"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        assert scores.get("novel2", 0) > scores.get("econ2", 0)

    def test_empty_liked_returns_empty(self, small_index):
        scores = recommend_scores(small_index, {}, fb_data={})
        assert scores == {}
