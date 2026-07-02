import numpy as np
from engine.index import VectorIndex
from engine.scorer import recommend_scores, recommend_scores_two_stage


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


class TestTwoStageTrailingEmptyReasons:
    """reduceat 세그먼트 경계 — 후보 스택 말미에 reason 0개 후보가 오면
    seg==len(CR) 인덱스로 IndexError 가 났던 회귀(twostage.py 와 동일 버그)."""

    def _make_index(self):
        idx = VectorIndex(dim=8)
        l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
        l2 = _norm([1, 0, 0, 0, 0.3, 0, 0, 0])
        idx.add_book("liked1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                     l1=l1, l2=l2,
                     reasons=[_norm([1, 0, 0, 0, 0.8, 0, 0, 0])])
        idx.add_book("withr", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                     l1=l1, l2=l2,
                     reasons=[_norm([1, 0, 0, 0, 0, 0.9, 0, 0])])
        # reason 0개 후보들 — desc 유사도를 달리해 argpartition 어느 순서로 뽑혀도
        # 말미(빈 세그먼트 트레일)가 생기도록 여러 개 배치.
        for k, tail in enumerate([0.9, 0.7, 0.5]):
            idx.add_book(f"empty{k}", desc=_norm([1, 0, 0, 0, tail, 0, 0, 0]),
                         l1=l1, l2=l2, reasons=[])
        idx.build_desc_matrix()
        return idx

    def test_trailing_empty_reason_candidate(self):
        idx = self._make_index()
        liked = {"liked1": {"rating": "good"}}
        fb = {"liked1": {"emb": _norm([1, 0, 0, 0, 0.7, 0.3, 0, 0]),
                         "is_dislike": False}}
        # top_n=4 → 읽은 책 제외 전 후보(withr + empty0~2) 전부 포함
        ts = recommend_scores_two_stage(idx, liked, fb, top_n=4)
        full = recommend_scores(idx, liked, fb)
        assert set(ts.keys()) == set(full.keys())
        # 빈-reason 후보를 0으로 채우되 직전 후보 마지막 reason 이 max 에서
        # 누락되면 안 됨(클램프 금지) → brute-force 와 점수 완전 동등으로 검증.
        for cid in full:
            assert abs(ts[cid] - full[cid]) < 1e-4, cid

    def test_all_candidates_empty_reasons(self):
        idx = self._make_index()
        liked = {"liked1": {"rating": "good"}, "withr": {"rating": "good"}}
        ts = recommend_scores_two_stage(idx, liked, {}, top_n=3)
        full = recommend_scores(idx, liked, {})
        assert set(ts.keys()) == set(full.keys())
        for cid in full:
            assert abs(ts[cid] - full[cid]) < 1e-4, cid
