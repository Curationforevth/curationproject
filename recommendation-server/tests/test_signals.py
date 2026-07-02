"""행동 신호(관심없음/읽고싶어요) — 설계: docs/superpowers/specs/2026-07-02-shelf-remove-not-interested-design.md

검증 대상:
- compute_input_hash: status/signals 가 해시에 반영(캐시 자동 무효화의 근거)
- stage1_hybrid: NI 후보 완전 제외 + NI 유사작 하락 / wishlist 유사작 상승
- batch_score_prestacked: wl/ni desc 항이 총점에 방향대로 반영
- /recommend 서빙 필터(_dedup_cached): 재계산 전에도 NI 책 즉시 제거
- 신호 없음(default) = 기존과 완전 동일(동등성 보전; twostage 동등성 스위트가 별도 가드)
"""
import numpy as np

from engine.cache import compute_input_hash
from engine.twostage import stage1_hybrid, batch_score_prestacked
from api.recommend import _dedup_cached
from tests.test_twostage_equivalence import _make_world, _norm_rows, DIM


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

_ROW = {"book_id": "b1", "rating": "good", "feedback_embedding": None, "status": "finished"}


def test_hash_includes_signals():
    base = compute_input_hash([_ROW])
    with_sig = compute_input_hash([_ROW], [{"book_id": "b9", "signal": "not_interested"}])
    assert base != with_sig
    # 신호 인자 생략 == 빈 신호 (기존 호출과 호환)
    assert base == compute_input_hash([_ROW], [])


def test_hash_includes_status():
    wishlist_row = dict(_ROW, status="wishlist", rating=None)
    finished_row = dict(_ROW, status="finished", rating=None)
    assert compute_input_hash([wishlist_row]) != compute_input_hash([finished_row])


def test_hash_order_invariant_with_signals():
    sigs = [{"book_id": "a", "signal": "not_interested"},
            {"book_id": "b", "signal": "not_interested"}]
    assert (compute_input_hash([_ROW], sigs)
            == compute_input_hash([_ROW], list(reversed(sigs))))


# ---------------------------------------------------------------------------
# stage1 — 제외 + 방향성
# ---------------------------------------------------------------------------

def _stage1(world, liked, **kw):
    index, bids, prestacked, dm, am, extra_query, rng = world
    return stage1_hybrid(liked, {}, dm, am, bids, top_n=len(bids), **kw)


def test_stage1_excludes_not_interested():
    world = _make_world(seed=1)
    _, bids, *_ = world
    liked = {bids[0]: {"rating": "good"}}
    ni = {bids[10], bids[11]}
    out = _stage1(world, liked, not_interested_ids=ni)
    assert ni.isdisjoint(out)
    # 신호 없으면 해당 책들이 후보에 존재(전수 top_n)
    out_base = _stage1(world, liked)
    assert ni.issubset(set(out_base))


def test_stage1_wishlist_boosts_similar_and_ni_demotes():
    """wishlist 책과 같은 desc 를 가진 후보는 순위 상승, NI 와 같은 desc 는 하락."""
    world = _make_world(seed=2)
    index, bids, prestacked, dm, am, extra_query, rng = world
    liked = {bids[0]: {"rating": "good"}}

    probe = bids[20]  # 순위 변화를 관찰할 후보
    twin = bids[21]   # probe 와 동일 desc 로 만든 신호 책
    dm[bids.index(twin)] = dm[bids.index(probe)]

    def rank(out, bid):
        return out.index(bid) if bid in out else len(out)

    base = _stage1(world, liked)
    up = _stage1(world, liked, wishlist_ids=[twin])
    down = _stage1(world, liked, not_interested_ids={twin})

    assert rank(up, probe) < rank(base, probe)    # 유사작 상승
    assert rank(down, probe) > rank(base, probe)  # 유사작 하락


# ---------------------------------------------------------------------------
# stage2 — wl/ni desc 항 방향성
# ---------------------------------------------------------------------------

def test_stage2_signal_terms_shift_scores():
    world = _make_world(seed=3)
    index, bids, prestacked, dm, am, extra_query, rng = world
    liked = {bids[0]: {"rating": "good"}}
    cands = bids[10:20]
    probe = cands[0]
    twin = bids[30]
    # twin 의 desc 를 probe 와 동일하게 — desc_of 는 per-book desc 를 우선 조회.
    index.get_book(twin).desc = index.get_book(probe).desc.copy()

    base = batch_score_prestacked(index, liked, {}, cands, prestacked)
    up = batch_score_prestacked(index, liked, {}, cands, prestacked,
                                wishlist_ids=[twin])
    down = batch_score_prestacked(index, liked, {}, cands, prestacked,
                                  not_interested_ids={twin})

    assert up[probe] > base[probe]
    assert down[probe] < base[probe]
    # 신호 없음 = 기존과 동일 (스칼라까지)
    again = batch_score_prestacked(index, liked, {}, cands, prestacked)
    assert base == again


# ---------------------------------------------------------------------------
# 서빙 필터 — 재계산 전 즉시 제거
# ---------------------------------------------------------------------------

def test_dedup_cached_filters_not_interested():
    rows = [{"book_id": f"b{i}", "score": 1.0 - i * 0.1,
             "title": f"t{i}", "author": "a", "cover_url": None} for i in range(5)]
    out = _dedup_cached(rows, limit=5, not_interested_ids={"b1", "b3"})
    got = [r.book_id for r in out]
    assert "b1" not in got and "b3" not in got
    assert len(got) == 3
    # 필터 없으면 전부
    assert len(_dedup_cached(rows, limit=5)) == 5
