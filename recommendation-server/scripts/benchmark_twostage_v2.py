#!/usr/bin/env python3
"""Two-stage v2 벤치마크 — 미해결 2가지 검증.

검증 항목:
  1. per-good-book retrieval recall — 좋아요 책 각각에서 top-N union → recall 개선 확인
  2. Stage 2 벡터화 _score_one — Python 루프 vs 배치 연산 속도 비교

사용법: cd recommendation-server && python3 -u scripts/benchmark_twostage_v2.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from engine.index import VectorIndex, BookVectors
from engine.loader import load_index
from engine.scorer import recommend_scores, _score_one, _maxsim
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT)

# ---------------------------------------------------------------------------
# 인덱스 로드
# ---------------------------------------------------------------------------
pkl_path = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
print("=" * 65)
print("Two-stage v2 벤치마크")
print("=" * 65)

print("\n인덱스 로드...")
index, books_meta, built_at = load_index(pkl_path)
all_bids = index.book_ids
N = len(all_bids)
print(f"  books: {N}, built_at: {built_at}")

# desc_matrix, agg_reason_matrix 준비
agg_reason_vecs = {}
for bid in all_bids:
    bv = index.get_book(bid)
    if bv.reasons:
        mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
        norm = np.linalg.norm(mean_vec)
        agg_reason_vecs[bid] = (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16)
    else:
        agg_reason_vecs[bid] = np.zeros(index.dim, dtype=np.float16)

bid_order = list(agg_reason_vecs.keys())
bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}
desc_matrix = np.stack([index.get_book(bid).desc for bid in bid_order]).astype(np.float32)
agg_reason_matrix = np.stack([agg_reason_vecs[bid] for bid in bid_order]).astype(np.float32)

# ---------------------------------------------------------------------------
# 시뮬레이션 유저 생성
# ---------------------------------------------------------------------------
rng = np.random.default_rng(42)

def make_sim_user(n_good: int, n_bad: int = 0):
    chosen = rng.choice(all_bids, size=n_good + n_bad, replace=False)
    liked_books = {}
    fb_data = {}
    for i, bid in enumerate(chosen):
        if i < n_good:
            liked_books[bid] = {"rating": "good"}
            bv = index.get_book(bid)
            if bv and bv.reasons and rng.random() > 0.5:
                fb_data[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": False}
        else:
            liked_books[bid] = {"rating": "bad"}
            bv = index.get_book(bid)
            if bv and bv.reasons:
                fb_data[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": True}
    return liked_books, fb_data

user_profiles = [
    ("6good_0bad", 6, 0),
    ("10good_2bad", 10, 2),
    ("20good_3bad", 20, 3),
]
sim_users = [(label, *make_sim_user(ng, nb)) for label, ng, nb in user_profiles]
print(f"  유저 프로파일: {[l for l, _, _ in sim_users]}")


# ===================================================================
# 검증 1: per-good-book retrieval recall
# ===================================================================
print("\n" + "=" * 65)
print("검증 1: per-good-book retrieval recall")
print("=" * 65)

TOP_K_FINAL = 20

def stage1_single_query(liked_books, top_n):
    """기존 방식: 모든 good book의 desc/reason을 한번에 비교."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    read_ids = set(liked_books.keys())

    good_descs = [index.get_book(bid).desc.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if not good_descs:
        return []

    good_desc_mat = np.stack(good_descs)
    desc_scores = (desc_matrix @ good_desc_mat.T).max(axis=1)

    good_agg = [agg_reason_vecs[bid].astype(np.float32) for bid in good_ids if bid in agg_reason_vecs]
    if good_agg:
        good_agg_mat = np.stack(good_agg)
        reason_scores = (agg_reason_matrix @ good_agg_mat.T).max(axis=1)
    else:
        reason_scores = np.zeros(N)

    combined = 3.0 * desc_scores + 2.0 * reason_scores
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            combined[idx] = -999.0

    top_idx = np.argsort(combined)[::-1][:top_n]
    return [bid_order[i] for i in top_idx]


def stage1_per_book(liked_books, fb_data, per_book_n):
    """개선: 좋아요 책 각각에서 top-N을 가져와 union.
    + fb_desc도 Stage 1 신호에 추가."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())

    candidate_scores = np.zeros(N, dtype=np.float32)

    # 각 good book에서 desc + agg_reason 유사도
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue

        d = bv.desc.astype(np.float32)
        desc_sim = desc_matrix @ d  # (N,)

        agg_r = agg_reason_vecs.get(bid)
        if agg_r is not None:
            reason_sim = agg_reason_matrix @ agg_r.astype(np.float32)
        else:
            reason_sim = np.zeros(N)

        candidate_scores += 3.0 * desc_sim + 2.0 * reason_sim

    # bad book 감점
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        d = bv.desc.astype(np.float32)
        desc_sim = desc_matrix @ d
        candidate_scores -= 1.5 * desc_sim

    # fb_desc 신호 추가
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_sim = desc_matrix @ fb["emb"].astype(np.float32)
        candidate_scores += sign * 2.0 * fb_sim

    # read_ids 제외
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            candidate_scores[idx] = -999.0

    # union 대신 전체 스코어 합산으로 top-N
    total_candidates = min(per_book_n * len(good_ids), N)
    total_candidates = min(total_candidates, 1500)  # cap
    top_idx = np.argsort(candidate_scores)[::-1][:total_candidates]
    return [bid_order[i] for i in top_idx]


for label, liked_books, fb_data in sim_users:
    print(f"\n  [{label}]")

    # Ground truth
    t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked_books, fb_data)
    t_full = time.perf_counter() - t0

    top_k_full = sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K_FINAL]
    ground_truth = set(bid for bid, _ in top_k_full)
    print(f"  Full scoring: {t_full:.1f}s")

    # A. 기존 single-query (비교용)
    for cand_size in [200, 500]:
        candidates = stage1_single_query(liked_books, cand_size)
        recall = len(ground_truth & set(candidates)) / len(ground_truth)
        print(f"  single-query top-{cand_size}: recall={recall:.0%}")

    # B. per-book retrieval
    for per_n in [50, 100, 150]:
        t0 = time.perf_counter()
        candidates = stage1_per_book(liked_books, fb_data, per_n)
        t_stage1 = time.perf_counter() - t0
        recall = len(ground_truth & set(candidates)) / len(ground_truth)
        print(f"  per-book×{per_n:>3} ({len(candidates):>5} cands): "
              f"recall={recall:.0%}  stage1={t_stage1*1000:.1f}ms")


# ===================================================================
# 검증 2: Stage 2 배치 벡터화 _score_one
# ===================================================================
print("\n" + "=" * 65)
print("검증 2: Stage 2 배치 스코어링 속도")
print("=" * 65)


def batch_score(index: VectorIndex, liked_books: dict, fb_data: dict,
                candidate_ids: list[str]) -> dict[str, float]:
    """벡터화된 배치 스코어링. _score_one의 배치 버전."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return {cid: 0.0 for cid in candidate_ids}

    # 후보 데이터 준비
    cand_books = [(cid, index.get_book(cid)) for cid in candidate_ids]
    cand_books = [(cid, bv) for cid, bv in cand_books if bv is not None]

    if not cand_books:
        return {}

    n_cands = len(cand_books)
    scores = np.zeros(n_cands, dtype=np.float32)

    # --- desc_score: max(dot(good_desc, cand_desc)) per candidate ---
    cand_descs = np.stack([bv.desc.astype(np.float32) for _, bv in cand_books])  # (n_cands, dim)
    good_descs = [index.get_book(bid).desc.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if good_descs:
        good_desc_mat = np.stack(good_descs)  # (n_good, dim)
        desc_sims = cand_descs @ good_desc_mat.T  # (n_cands, n_good)
        desc_scores = desc_sims.max(axis=1)  # (n_cands,)
    else:
        desc_scores = np.zeros(n_cands)

    # --- L1_score ---
    cand_l1s = np.stack([bv.l1.astype(np.float32) for _, bv in cand_books])
    good_l1s = [index.get_book(bid).l1.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if good_l1s:
        good_l1_mat = np.stack(good_l1s)
        l1_scores = (cand_l1s @ good_l1_mat.T).max(axis=1)
    else:
        l1_scores = np.zeros(n_cands)

    # --- L2_score ---
    cand_l2s = np.stack([bv.l2.astype(np.float32) for _, bv in cand_books])
    good_l2s = [index.get_book(bid).l2.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if good_l2s:
        good_l2_mat = np.stack(good_l2s)
        l2_scores = (cand_l2s @ good_l2_mat.T).max(axis=1)
    else:
        l2_scores = np.zeros(n_cands)

    # --- fb_desc_score: mean(sign * dot(fb_emb, cand_desc)) ---
    fb_desc_scores = np.zeros(n_cands)
    fb_entries = [(bid, fb) for bid, fb in fb_data.items()
                  if liked_books.get(bid, {}).get("rating") != "neutral"]
    if fb_entries:
        fb_vals = np.zeros((n_cands, len(fb_entries)))
        for j, (bid, fb) in enumerate(fb_entries):
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_vals[:, j] = sign * (cand_descs @ fb["emb"].astype(np.float32))
        fb_desc_scores = fb_vals.mean(axis=1)

    # --- reason_score: 가변 길이라 per-candidate 루프 필요하지만 내부 벡터화 ---
    reason_scores = np.zeros(n_cands)

    # good book reason 데이터 사전 준비
    good_data = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        has_fb = fb is not None and not fb["is_dislike"]
        good_data.append((bv, fb if has_fb else None))

    bad_data = []
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        has_fb = fb is not None and fb["is_dislike"]
        bad_data.append((bv, fb if has_fb else None))

    for i, (cid, cand_bv) in enumerate(cand_books):
        if not cand_bv.reasons:
            continue

        cand_r = np.stack(cand_bv.reasons).astype(np.float32)  # (n_cand_r, dim)
        weighted_maxsims = []

        for bv, fb in good_data:
            if fb:
                fb_sim = float((cand_r @ fb["emb"].astype(np.float32)).max())
                if bv.reasons:
                    q = np.stack(bv.reasons).astype(np.float32)
                    sims = q @ cand_r.T
                    r_sim = float(sims.max(axis=1).mean())
                else:
                    r_sim = 0.0
                weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                if bv.reasons:
                    q = np.stack(bv.reasons).astype(np.float32)
                    sims = q @ cand_r.T
                    r_sim = float(sims.max(axis=1).mean())
                else:
                    r_sim = 0.0
                weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)

        for bv, fb in bad_data:
            if fb:
                fb_sim = float((cand_r @ fb["emb"].astype(np.float32)).max())
                if bv.reasons:
                    q = np.stack(bv.reasons).astype(np.float32)
                    sims = q @ cand_r.T
                    r_sim = float(sims.max(axis=1).mean())
                else:
                    r_sim = 0.0
                weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                if bv.reasons:
                    q = np.stack(bv.reasons).astype(np.float32)
                    sims = q @ cand_r.T
                    r_sim = float(sims.max(axis=1).mean())
                else:
                    r_sim = 0.0
                weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)

        if weighted_maxsims:
            reason_scores[i] = float(np.mean(weighted_maxsims))

    # 최종 스코어
    final = (W_REASON * reason_scores + W_DESC * desc_scores +
             W_L1 * l1_scores + W_L2 * l2_scores + W_FB_DESC * fb_desc_scores)

    return {cid: float(final[i]) for i, (cid, _) in enumerate(cand_books)}


# 정확성 검증: batch_score vs _score_one
print("\n정확성 검증 (batch vs loop)...")
test_liked, test_fb = sim_users[0][1], sim_users[0][2]
test_candidates = [bid for bid in all_bids[:100] if bid not in test_liked]

loop_scores = {}
active = {bid: d for bid, d in test_liked.items() if d["rating"] != "neutral"}
for cid in test_candidates:
    loop_scores[cid] = _score_one(index, active, test_fb, cid)

batch_scores_result = batch_score(index, test_liked, test_fb, test_candidates)

max_diff = 0.0
for cid in test_candidates:
    if cid in batch_scores_result:
        diff = abs(loop_scores[cid] - batch_scores_result[cid])
        max_diff = max(max_diff, diff)

print(f"  max score diff (loop vs batch): {max_diff:.6f}")
print(f"  정확성: {'PASS' if max_diff < 0.01 else 'FAIL'}")

# 속도 비교
print("\n속도 비교...")
for label, liked_books, fb_data in sim_users:
    good_count = sum(1 for d in liked_books.values() if d["rating"] == "good")
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    read_ids = set(liked_books.keys())

    for cand_size in [200, 500, 1000]:
        # per-book retrieval로 후보 선별
        candidates = stage1_per_book(liked_books, fb_data, cand_size // max(good_count, 1))
        candidates = candidates[:cand_size]

        # Loop 방식
        t0 = time.perf_counter()
        for cid in candidates:
            _score_one(index, active, fb_data, cid)
        t_loop = time.perf_counter() - t0

        # Batch 방식
        t0 = time.perf_counter()
        batch_score(index, liked_books, fb_data, candidates)
        t_batch = time.perf_counter() - t0

        speedup = t_loop / t_batch if t_batch > 0 else 0
        print(f"  [{label}] {cand_size} cands: "
              f"loop={t_loop*1000:.0f}ms  batch={t_batch*1000:.0f}ms  "
              f"speedup={speedup:.1f}x")


# ===================================================================
# 검증 3: 전체 Two-stage 파이프라인 end-to-end
# ===================================================================
print("\n" + "=" * 65)
print("검증 3: 전체 Two-stage end-to-end (vs full scoring)")
print("=" * 65)

for label, liked_books, fb_data in sim_users:
    good_count = sum(1 for d in liked_books.values() if d["rating"] == "good")

    # Full scoring (ground truth)
    t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked_books, fb_data)
    t_full = time.perf_counter() - t0
    top20_full = sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:20]
    gt = set(bid for bid, _ in top20_full)

    # Two-stage: per-book retrieval + batch scoring
    t0 = time.perf_counter()
    candidates = stage1_per_book(liked_books, fb_data, 100)
    t_s1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    s2_scores = batch_score(index, liked_books, fb_data, candidates)
    t_s2 = time.perf_counter() - t0

    top20_ts = sorted(s2_scores.items(), key=lambda x: x[1], reverse=True)[:20]
    ts_set = set(bid for bid, _ in top20_ts)

    recall = len(gt & ts_set) / len(gt) if gt else 0
    total_ts = t_s1 + t_s2

    print(f"\n  [{label}]")
    print(f"  Full:      {t_full:.1f}s")
    print(f"  Two-stage: {total_ts*1000:.0f}ms (S1={t_s1*1000:.0f}ms + S2={t_s2*1000:.0f}ms)")
    print(f"  Speedup:   {t_full/total_ts:.0f}x")
    print(f"  Recall:    {recall:.0%} (top-20)")
    print(f"  Candidates: {len(candidates)}")

print("\n" + "=" * 65)
print("벤치마크 완료")
print("=" * 65)
