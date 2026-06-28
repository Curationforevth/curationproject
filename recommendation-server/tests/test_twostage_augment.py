"""C2 라이브 취향 보강 — 인덱스 밖 좋아요 책을 extra_query 로 주입했을 때
stage1_hybrid / batch_score_prestacked 가 취향에 반영하는지 검증."""
import numpy as np

from engine.index import BookVectors, VectorIndex
from engine.twostage import stage1_hybrid, batch_score_prestacked


def _unit(seed, dim=2000):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_stage1_returns_candidates_when_all_good_books_out_of_index():
    dim = 2000
    bid_order = ["c1", "c2", "c3"]
    dm = np.stack([_unit(1, dim), _unit(2, dim), _unit(3, dim)]).astype(np.float16)
    am = np.zeros((3, dim), dtype=np.float16)
    liked = {"USER_BOOK": {"rating": "good"}}  # not in bid_order
    extra = {"USER_BOOK": BookVectors(reasons=[], desc=dm[0].astype(np.float32),
                                      l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    out = stage1_hybrid(liked, {}, dm, am, bid_order, top_n=3, extra_query=extra)
    assert out, "인덱스 밖 good 책 주입 시에도 후보가 나와야 함"
    assert "c1" in out  # USER_BOOK desc == c1 desc → 최상위


def test_batch_score_uses_extra_query_good_book():
    dim = 2000
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim), l1=np.zeros(dim), l2=np.zeros(dim))
    liked = {"USER_BOOK": {"rating": "good"}}
    extra = {"USER_BOOK": BookVectors(reasons=[], desc=_unit(1, dim),
                                      l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    scores = batch_score_prestacked(idx, liked, {}, ["cand"], {}, extra_query=extra)
    assert "cand" in scores
    assert scores["cand"] > 0  # USER_BOOK desc ≈ cand desc → desc_score 양수


def test_stage1_no_extra_query_unchanged_behavior():
    """extra_query 미지정 시 기존 동작(인덱스 밖 책 무시) 유지 — 하위호환."""
    dim = 2000
    bid_order = ["c1", "c2"]
    dm = np.stack([_unit(1, dim), _unit(2, dim)]).astype(np.float16)
    am = np.zeros((2, dim), dtype=np.float16)
    # 좋아요 책이 전부 인덱스 밖 + extra_query 없음 → 기존대로 []
    out = stage1_hybrid({"OUT": {"rating": "good"}}, {}, dm, am, bid_order, top_n=2)
    assert out == []


def test_compute_scored_books_threads_extra_query():
    from engine.recommend_core import compute_scored_books
    dim = 2000
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim), l1=np.zeros(dim), l2=np.zeros(dim))
    extra = {"UB": BookVectors(reasons=[], desc=_unit(1, dim),
                               l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    out = compute_scored_books(
        index=idx, liked_books={"UB": {"rating": "good"}}, fb_data={},
        prestacked_reasons={}, desc_matrix_f16=np.stack([_unit(1, dim)]).astype(np.float16),
        agg_reason_matrix_f16=np.zeros((1, dim), np.float16), bid_order=["cand"],
        extra_query=extra)
    assert out and out[0][0] == "cand"
