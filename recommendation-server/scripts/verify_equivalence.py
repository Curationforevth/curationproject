#!/usr/bin/env python3
"""Layer 1 — 실인덱스 오프라인 전수 동등성 검증 (Phase 2 벡터화).

신규 벡터화 twostage vs 검증 기준선(twostage_reference)을 **현행 prod 인덱스
전체**(data/index.pkl)에서 비교한다. OpenAI/DB 호출 없음(완전 오프라인).

유저 셋: 18 페르소나(scripts/test_data/personas.json, fb=책 reason 벡터 대체)
        + 랜덤 50 + desc-클러스터 30 + extra_query 주입 10 = 108명.

합격 기준(설계 문서 §3 Layer 1):
  - stage1 후보 150 집합 overlap ≥ 149/150 (전원)
  - 최종 top-20 리스트 완전 동일 (전원)
  - stage2 점수 max|Δ| < 1e-3

사용법: cd recommendation-server && python3 -u scripts/verify_equivalence.py
종료코드: 0=PASS, 1=FAIL
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from engine.loader import load_index
from engine.index import BookVectors
from engine.twostage import stage1_hybrid, batch_score_prestacked
from engine.twostage_reference import (stage1_hybrid_reference,
                                       batch_score_prestacked_reference)
from config import STAGE1_TOP_N, CACHE_TOP_N

PKL = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
PERSONAS = os.path.join(os.path.dirname(__file__), "..", "..",
                        "scripts", "test_data", "personas.json")
TOP_K = 20

print("=" * 72)
print("Layer 1 — 실인덱스 전수 동등성 검증 (ref vs vectorized)")
print("=" * 72)

index, meta, built_at, prestacked, desc_mat, agg_mat, bid_order = load_index(PKL)
if prestacked is None:
    print("FAIL: v4 번들이 아님 — prestacked 없음")
    sys.exit(1)

# prod 서빙 상태 재현 (main.py lifespan 과 동일)
index.attach_desc_matrix(desc_mat, bid_order)
index.strip_unused_genre_vectors()

N = len(bid_order)
DIM = index.dim
bid_set = set(bid_order)
print(f"books={N} dim={DIM} built_at={built_at}")

rng = np.random.default_rng(20260702)


def _fb_from_reasons(bid, is_dislike):
    """OpenAI 없이 결정적 fb 임베딩 — 해당 책의 첫 reason 벡터를 사용."""
    pr = prestacked.get(bid)
    if pr is None or pr.shape[0] == 0:
        return None
    return {"emb": pr[0].astype(np.float32), "is_dislike": is_dislike}


def make_personas():
    users = []
    try:
        personas = json.load(open(PERSONAS))["personas"]
    except Exception as e:
        print(f"  (personas.json 로드 실패 — 스킵: {e})")
        return users
    for p in personas:
        liked, fb = {}, {}
        for b in p.get("books", []):
            bid = b.get("book_id")
            if not bid or bid not in bid_set:
                continue  # 구 DB id — 인덱스 밖이면 제외(주입 시나리오는 별도 셋)
            rating = b.get("rating", "good")
            liked[bid] = {"rating": rating}
            if b.get("review_text"):
                f = _fb_from_reasons(bid, rating == "bad")
                if f:
                    fb[bid] = f
        if liked:
            users.append((f"persona:{p['id']}", liked, fb, None))
    return users


def make_random(n_users=50):
    users = []
    for i in range(n_users):
        n_good = int(rng.integers(6, 25))
        n_bad = int(rng.integers(0, 5))
        chosen = rng.choice(bid_order, size=n_good + n_bad, replace=False)
        liked, fb = {}, {}
        for j, bid in enumerate(chosen):
            rating = "good" if j < n_good else "bad"
            liked[bid] = {"rating": rating}
            if rng.random() > 0.5:
                f = _fb_from_reasons(bid, rating == "bad")
                if f:
                    fb[bid] = f
        users.append((f"random:{i}", liked, fb, None))
    return users


def make_clustered(n_users=30):
    """desc 유사도 기반 현실 유저 — 1~3개 취향 축에서 좋아요."""
    users = []
    for i in range(n_users):
        n_axis = int(rng.integers(1, 4))
        liked, fb = {}, {}
        for _ in range(n_axis):
            seed = str(rng.choice(bid_order))
            sims = index.similar_by_desc(seed, limit=8)
            picks = [seed] + [b for b, _ in sims[:int(rng.integers(3, 7))]]
            for bid in picks:
                liked[bid] = {"rating": "good"}
                if rng.random() > 0.6:
                    f = _fb_from_reasons(bid, False)
                    if f:
                        fb[bid] = f
        # 다른 축에서 싫어요 1~2
        for _ in range(int(rng.integers(0, 3))):
            bid = str(rng.choice(bid_order))
            if bid not in liked:
                liked[bid] = {"rating": "bad"}
        users.append((f"clustered:{i}", liked, fb, None))
    return users


def make_extra_query(n_users=10):
    """인덱스 밖 유저책 주입(extra_query) 시나리오."""
    users = []
    for i in range(n_users):
        n_good = int(rng.integers(4, 10))
        chosen = rng.choice(bid_order, size=n_good, replace=False)
        liked = {bid: {"rating": "good"} for bid in chosen}
        extra = {}
        for k in range(int(rng.integers(1, 4))):
            v = rng.normal(size=DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            xid = f"EXTRA_{i}_{k}"
            extra[xid] = BookVectors(reasons=[], desc=v,
                                     l1=np.zeros(DIM, np.float32),
                                     l2=np.zeros(DIM, np.float32))
            liked[xid] = {"rating": "good" if k % 2 == 0 else "bad"}
        users.append((f"extra:{i}", liked, {}, extra))
    return users


users = make_personas() + make_random() + make_clustered() + make_extra_query()
print(f"users={len(users)} (persona/random/clustered/extra)")

fails = []
overlap_min = STAGE1_TOP_N
max_delta = 0.0
t_ref_all, t_new_all = [], []

for label, liked, fb, extra in users:
    t0 = time.perf_counter()
    c_ref = stage1_hybrid_reference(liked, fb, desc_mat, agg_mat, bid_order,
                                    top_n=STAGE1_TOP_N, extra_query=extra)
    s_ref = batch_score_prestacked_reference(index, liked, fb, c_ref, prestacked,
                                             extra_query=extra)
    t_ref = time.perf_counter() - t0

    t0 = time.perf_counter()
    c_new = stage1_hybrid(liked, fb, desc_mat, agg_mat, bid_order,
                          top_n=STAGE1_TOP_N, extra_query=extra)
    s_new = batch_score_prestacked(index, liked, fb, c_new, prestacked,
                                   extra_query=extra)
    t_new = time.perf_counter() - t0
    t_ref_all.append(t_ref)
    t_new_all.append(t_new)

    ov = len(set(c_ref) & set(c_new))
    overlap_min = min(overlap_min, ov)
    if ov < min(len(c_ref), STAGE1_TOP_N) - 1:
        fails.append(f"{label}: stage1 overlap {ov}/{len(c_ref)}")

    # stage2 점수 동등성 (동일 후보셋 기준)
    s_new_on_ref = batch_score_prestacked(index, liked, fb, c_ref, prestacked,
                                          extra_query=extra)
    for cid in s_ref:
        d = abs(s_ref[cid] - s_new_on_ref.get(cid, float("nan")))
        max_delta = max(max_delta, d)
        if not (d < 1e-3):
            fails.append(f"{label}: stage2 Δ={d:.6f} cid={cid}")
            break

    # 최종 top-20 (end-to-end, 각자 자기 파이프라인)
    top_ref = [b for b, _ in sorted(s_ref.items(), key=lambda x: x[1], reverse=True)[:TOP_K]]
    top_new = [b for b, _ in sorted(s_new.items(), key=lambda x: x[1], reverse=True)[:TOP_K]]
    if top_ref != top_new:
        diff = len(set(top_ref) & set(top_new))
        fails.append(f"{label}: top-{TOP_K} mismatch (공통 {diff}/{TOP_K})")

print("\n" + "=" * 72)
print(f"stage1 후보 overlap 최소: {overlap_min}/{STAGE1_TOP_N}")
print(f"stage2 점수 max|Δ|: {max_delta:.2e}")
print(f"레이턴시(s1+s2, 로컬): ref p50={np.median(t_ref_all)*1000:.0f}ms "
      f"p95={np.percentile(t_ref_all, 95)*1000:.0f}ms | "
      f"new p50={np.median(t_new_all)*1000:.0f}ms "
      f"p95={np.percentile(t_new_all, 95)*1000:.0f}ms | "
      f"중앙값 speedup ×{np.median(t_ref_all)/max(np.median(t_new_all), 1e-9):.1f}")

if fails:
    print(f"\nFAIL ({len(fails)}건):")
    for f in fails[:20]:
        print("  -", f)
    sys.exit(1)
print(f"\nPASS — {len(users)}명 전원 top-{TOP_K} 동일, 후보 overlap ≥ {overlap_min}/{STAGE1_TOP_N}")
