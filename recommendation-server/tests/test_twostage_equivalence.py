"""Phase 2 벡터화 동등성 가드 — 신규 twostage vs 검증 기준선(twostage_reference).

벡터화는 float 연산 순서만 바꾸는 수학적 동치 변환이어야 한다. 여기서는
랜덤 합성 인덱스 + 엣지케이스 전 조합으로 점수(max|Δ|<1e-4)·순위 동일을 강제한다.
실인덱스 전수 검증은 scripts/verify_equivalence.py (Layer 1).
"""
import numpy as np
import pytest

from engine.index import VectorIndex, BookVectors
from engine.twostage import stage1_hybrid, batch_score_prestacked
from engine.twostage_reference import (stage1_hybrid_reference,
                                       batch_score_prestacked_reference)

DIM = 32


def _norm_rows(a):
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return a / n


def _make_world(seed, n_books=50, with_tier=True):
    """랜덤 인덱스 + prestacked + stage1 행렬 + extra_query 생성.

    reasons 는 0~4개(빈 책 포함, '마지막 책이 빈 reason'이 되도록 강제 —
    concat/reduceat 경계 버그를 잡는다).
    """
    rng = np.random.default_rng(seed)
    index = VectorIndex(dim=DIM)
    bids = [f"b{i:03d}" for i in range(n_books)]
    prestacked = {}
    for i, bid in enumerate(bids):
        # 마지막 2권은 reason 없음(경계) / 나머지 0~4 랜덤
        n_r = 0 if i >= n_books - 2 else int(rng.integers(0, 5))
        reasons = [_norm_rows(rng.normal(size=DIM)).astype(np.float32) for _ in range(n_r)]
        desc = _norm_rows(rng.normal(size=DIM)).astype(np.float32)
        l1 = _norm_rows(rng.normal(size=DIM)).astype(np.float32)
        l2 = _norm_rows(rng.normal(size=DIM)).astype(np.float32)
        index.add_book(bid, reasons, desc, l1, l2)
        if n_r:
            prestacked[bid] = np.stack(reasons).astype(np.float16)
        else:
            prestacked[bid] = np.zeros((0, DIM), dtype=np.float16)
    # 일부 책은 prestacked 에서 아예 제외 → bv.reasons fallback 경로
    for bid in bids[5:8]:
        del prestacked[bid]

    index.build_desc_matrix()
    desc_matrix_f16 = index._desc_matrix.astype(np.float16)
    agg = []
    for bid in bids:
        rs = index.get_book(bid).reasons
        if rs:
            m = np.stack(rs).mean(axis=0)
            n = np.linalg.norm(m)
            agg.append((m / n if n > 0 else m))
        else:
            agg.append(np.zeros(DIM, np.float32))
    agg_matrix_f16 = np.stack(agg).astype(np.float16)

    if with_tier:
        index._candidate_tier = {bids[3]: "kakao_desc", bids[4]: "minimal"}

    extra_query = {
        "EXTRA_G": BookVectors(reasons=[], desc=_norm_rows(rng.normal(size=DIM)).astype(np.float32),
                               l1=np.zeros(DIM, np.float32), l2=np.zeros(DIM, np.float32)),
        "EXTRA_B": BookVectors(reasons=[], desc=_norm_rows(rng.normal(size=DIM)).astype(np.float32),
                               l1=np.zeros(DIM, np.float32), l2=np.zeros(DIM, np.float32)),
    }
    return index, bids, prestacked, desc_matrix_f16, agg_matrix_f16, extra_query, rng


def _make_user(bids, rng, n_good=8, n_bad=3, with_fb=True, with_extra=True,
               with_neutral=True, with_missing=True):
    chosen = list(rng.choice(bids, size=n_good + n_bad, replace=False))
    liked, fb = {}, {}
    for i, bid in enumerate(chosen):
        rating = "good" if i < n_good else "bad"
        liked[bid] = {"rating": rating}
        if with_fb and rng.random() > 0.4:
            fb[bid] = {"emb": _norm_rows(rng.normal(size=DIM)).astype(np.float32),
                       "is_dislike": rating == "bad"}
    if with_neutral:
        neutral_bid = next(b for b in bids if b not in liked)
        liked[neutral_bid] = {"rating": "neutral"}
        fb[neutral_bid] = {"emb": _norm_rows(rng.normal(size=DIM)).astype(np.float32),
                           "is_dislike": False}  # neutral fb 는 무시돼야 함
    if with_extra:
        liked["EXTRA_G"] = {"rating": "good"}
        liked["EXTRA_B"] = {"rating": "bad"}
    if with_missing:
        liked["GHOST"] = {"rating": "good"}  # 인덱스에도 extra 에도 없음 → skip 경로
    return liked, fb


SEEDS = [7, 42, 1234]


class TestStage1Equivalence:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_full_mix(self, seed):
        index, bids, ps, dm, am, extra, rng = _make_world(seed)
        liked, fb = _make_user(bids, rng)
        ref = stage1_hybrid_reference(liked, fb, dm, am, bids, top_n=15, extra_query=extra)
        new = stage1_hybrid(liked, fb, dm, am, bids, top_n=15, extra_query=extra)
        assert new == ref

    @pytest.mark.parametrize("seed", SEEDS)
    def test_no_feedback(self, seed):
        index, bids, ps, dm, am, extra, rng = _make_world(seed)
        liked, _ = _make_user(bids, rng, with_fb=False)
        assert stage1_hybrid(liked, {}, dm, am, bids, top_n=15, extra_query=extra) == \
            stage1_hybrid_reference(liked, {}, dm, am, bids, top_n=15, extra_query=extra)

    def test_no_extra_no_bad(self):
        index, bids, ps, dm, am, extra, rng = _make_world(99)
        liked, fb = _make_user(bids, rng, n_bad=0, with_extra=False, with_missing=False)
        assert stage1_hybrid(liked, fb, dm, am, bids, top_n=10) == \
            stage1_hybrid_reference(liked, fb, dm, am, bids, top_n=10)

    def test_only_bad_returns_empty(self):
        index, bids, ps, dm, am, extra, rng = _make_world(99)
        liked = {bids[0]: {"rating": "bad"}}
        assert stage1_hybrid(liked, {}, dm, am, bids, top_n=10) == []

    def test_extra_good_only(self):
        """좋아요가 전부 인덱스 밖(extra_query)인 콜드 유저."""
        index, bids, ps, dm, am, extra, rng = _make_world(11)
        liked = {"EXTRA_G": {"rating": "good"}, "EXTRA_B": {"rating": "bad"}}
        assert stage1_hybrid(liked, {}, dm, am, bids, top_n=10, extra_query=extra) == \
            stage1_hybrid_reference(liked, {}, dm, am, bids, top_n=10, extra_query=extra)


class TestStage2Equivalence:
    def _compare(self, index, liked, fb, cands, ps, extra=None, **weights):
        ref = batch_score_prestacked_reference(index, liked, fb, cands, ps,
                                               extra_query=extra, **weights)
        new = batch_score_prestacked(index, liked, fb, cands, ps,
                                     extra_query=extra, **weights)
        assert set(new.keys()) == set(ref.keys())
        for cid in ref:
            assert abs(new[cid] - ref[cid]) < 1e-4, \
                f"{cid}: new={new[cid]:.6f} ref={ref[cid]:.6f}"
        rank_ref = sorted(ref, key=ref.get, reverse=True)
        rank_new = sorted(new, key=new.get, reverse=True)
        assert rank_new == rank_ref

    @pytest.mark.parametrize("seed", SEEDS)
    def test_full_mix(self, seed):
        index, bids, ps, dm, am, extra, rng = _make_world(seed)
        liked, fb = _make_user(bids, rng)
        cands = [b for b in bids if b not in liked] + ["NONEXISTENT"]
        self._compare(index, liked, fb, cands, ps, extra=extra)

    @pytest.mark.parametrize("seed", SEEDS)
    def test_no_feedback(self, seed):
        index, bids, ps, dm, am, extra, rng = _make_world(seed)
        liked, _ = _make_user(bids, rng, with_fb=False)
        cands = [b for b in bids if b not in liked]
        self._compare(index, liked, {}, cands, ps, extra=extra)

    def test_trailing_empty_reason_candidate(self):
        """마지막 후보가 reason 0개 — concat/reduceat 경계."""
        index, bids, ps, dm, am, extra, rng = _make_world(7)
        liked, fb = _make_user(bids, rng)
        empties = [b for b in bids if ps.get(b) is not None and ps[b].shape[0] == 0
                   and b not in liked]
        cands = [b for b in bids if b not in liked and b not in empties] + empties
        assert len(empties) >= 1
        self._compare(index, liked, fb, cands, ps, extra=extra)

    def test_all_candidates_empty_reasons(self):
        index, bids, ps, dm, am, extra, rng = _make_world(21)
        liked, fb = _make_user(bids, rng)
        empties = [b for b in bids if ps.get(b) is not None and ps[b].shape[0] == 0
                   and b not in liked]
        assert empties
        self._compare(index, liked, fb, empties, ps, extra=extra)

    def test_stripped_desc_index(self):
        """prod 형태: per-book desc 가 strip 되고 _desc_matrix 만 있는 인덱스."""
        index, bids, ps, dm, am, extra, rng = _make_world(42)
        for bid in bids:
            index.get_book(bid).desc = None  # desc_of 가 행렬 경로로 조회
        liked, fb = _make_user(bids, rng)
        cands = [b for b in bids if b not in liked]
        self._compare(index, liked, fb, cands, ps, extra=extra)

    def test_l1_l2_weights_nonzero(self):
        index, bids, ps, dm, am, extra, rng = _make_world(7)
        liked, fb = _make_user(bids, rng, with_extra=False, with_missing=False)
        cands = [b for b in bids if b not in liked]
        self._compare(index, liked, fb, cands, ps,
                      w_l1=1.5, w_l2=0.7)

    def test_bad_only_user(self):
        index, bids, ps, dm, am, extra, rng = _make_world(7)
        liked = {bids[0]: {"rating": "bad"}, bids[1]: {"rating": "bad"}}
        fb = {bids[0]: {"emb": _norm_rows(np.random.default_rng(1).normal(size=DIM)).astype(np.float32),
                        "is_dislike": True}}
        cands = [b for b in bids if b not in liked]
        self._compare(index, liked, fb, cands, ps)

    def test_empty_user_returns_empty(self):
        index, bids, ps, dm, am, extra, rng = _make_world(7)
        assert batch_score_prestacked(index, {}, {}, bids, ps) == {}

    def test_tier_penalty_applied_identically(self):
        """kakao_desc/minimal 후보의 positive-part 페널티가 신규 경로에도 동일."""
        index, bids, ps, dm, am, extra, rng = _make_world(7, with_tier=True)
        liked, fb = _make_user(bids, rng, with_extra=False, with_missing=False)
        cands = [b for b in ("b003", "b004") if b not in liked]
        if cands:
            self._compare(index, liked, fb, cands, ps)
