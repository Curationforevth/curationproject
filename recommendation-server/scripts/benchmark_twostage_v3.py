#!/usr/bin/env python3
"""Two-stage v3 벤치마크 — 최적화 + 리스크 검증.

검증 항목:
  1. Hybrid Stage 1: single-query ∪ per-book → 6good recall 개선
  2. Padded 3D tensor reason scoring → 200ms 목표 도전
  3. 최적 후보 수 탐색 (recall vs latency 곡선)
  4. 리스크 검증:
     a. reason 분포 편향 — 평균벡터가 왜곡되는 책 비율
     b. 새 신호(desc만, reason 없는 책) 처리
     c. 유저 취향 다양성 vs recall 관계
     d. 5만권 스케일 시뮬레이션 (실측 기반)

사용법: cd recommendation-server && python3 -u scripts/benchmark_twostage_v3.py
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
pkl_path = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
print("=" * 65)
print("Two-stage v3 벤치마크 — 최적화 + 리스크 검증")
print("=" * 65)

print("\n인덱스 로드...")
index, books_meta, built_at = load_index(pkl_path)
all_bids = index.book_ids
N = len(all_bids)
DIM = index.dim
print(f"  books: {N}, dim: {DIM}")

# 사전 계산
agg_reason_vecs = {}
reason_counts = {}
for bid in all_bids:
    bv = index.get_book(bid)
    rc = len(bv.reasons)
    reason_counts[bid] = rc
    if bv.reasons:
        mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
        norm = np.linalg.norm(mean_vec)
        agg_reason_vecs[bid] = (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16)
    else:
        agg_reason_vecs[bid] = np.zeros(DIM, dtype=np.float16)

bid_order = list(agg_reason_vecs.keys())
bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}
desc_matrix = np.stack([index.get_book(bid).desc for bid in bid_order]).astype(np.float32)
agg_reason_matrix = np.stack([agg_reason_vecs[bid] for bid in bid_order]).astype(np.float32)

total_reasons = sum(reason_counts.values())
avg_reasons = total_reasons / N
max_reasons = max(reason_counts.values())
print(f"  reasons: total={total_reasons}, avg={avg_reasons:.1f}, max={max_reasons}")

rng = np.random.default_rng(42)


def make_sim_user(n_good, n_bad=0):
    chosen = rng.choice(all_bids, size=n_good + n_bad, replace=False)
    liked_books, fb_data = {}, {}
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


# ===================================================================
# 1. Hybrid Stage 1: single-query ∪ per-book
# ===================================================================
print("\n" + "=" * 65)
print("1. Hybrid Stage 1 — single-query ∪ per-book")
print("=" * 65)


def stage1_single_query(liked_books, fb_data, top_n):
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    read_ids = set(liked_books.keys())
    good_descs = [index.get_book(bid).desc.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if not good_descs:
        return [], np.zeros(N)
    good_desc_mat = np.stack(good_descs)
    d_scores = (desc_matrix @ good_desc_mat.T).max(axis=1)
    good_agg = [agg_reason_vecs[bid].astype(np.float32) for bid in good_ids if bid in agg_reason_vecs]
    if good_agg:
        r_scores = (agg_reason_matrix @ np.stack(good_agg).T).max(axis=1)
    else:
        r_scores = np.zeros(N)
    # fb_desc 포함
    fb_scores = np.zeros(N)
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_scores += sign * (desc_matrix @ fb["emb"].astype(np.float32))
    combined = 3.0 * d_scores + 2.0 * r_scores + 2.0 * fb_scores
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            combined[idx] = -999.0
    top_idx = np.argsort(combined)[::-1][:top_n]
    return [bid_order[i] for i in top_idx], combined


def stage1_per_book(liked_books, fb_data, total_n):
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())
    scores = np.zeros(N, dtype=np.float32)
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        d = bv.desc.astype(np.float32)
        scores += 3.0 * (desc_matrix @ d)
        agg_r = agg_reason_vecs.get(bid)
        if agg_r is not None:
            scores += 2.0 * (agg_reason_matrix @ agg_r.astype(np.float32))
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        scores -= 1.5 * (desc_matrix @ bv.desc.astype(np.float32))
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        scores += sign * 2.0 * (desc_matrix @ fb["emb"].astype(np.float32))
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            scores[idx] = -999.0
    top_idx = np.argsort(scores)[::-1][:total_n]
    return [bid_order[i] for i in top_idx], scores


def stage1_hybrid(liked_books, fb_data, single_n, per_book_n):
    """single-query top-N과 per-book top-N의 union."""
    t0 = time.perf_counter()
    sq_cands, sq_scores = stage1_single_query(liked_books, fb_data, single_n)
    pb_cands, pb_scores = stage1_per_book(liked_books, fb_data, per_book_n)
    elapsed = time.perf_counter() - t0

    # 두 점수를 정규화해서 합산, union으로 후보 선정
    # min-max 정규화
    sq_valid = sq_scores[sq_scores > -900]
    pb_valid = pb_scores[pb_scores > -900]

    if len(sq_valid) > 1:
        sq_norm = (sq_scores - sq_valid.min()) / (sq_valid.max() - sq_valid.min() + 1e-9)
    else:
        sq_norm = np.zeros_like(sq_scores)

    if len(pb_valid) > 1:
        pb_norm = (pb_scores - pb_valid.min()) / (pb_valid.max() - pb_valid.min() + 1e-9)
    else:
        pb_norm = np.zeros_like(pb_scores)

    # 마스킹 유지
    for rid in set(liked_books.keys()):
        idx = bid_to_idx.get(rid)
        if idx is not None:
            sq_norm[idx] = -999.0
            pb_norm[idx] = -999.0

    combined = sq_norm + pb_norm
    total_n = max(single_n, per_book_n)
    top_idx = np.argsort(combined)[::-1][:total_n]
    candidates = [bid_order[i] for i in top_idx]
    return candidates, elapsed


TOP_K = 20
user_profiles = [
    ("6good", 6, 0),
    ("10good_2bad", 10, 2),
    ("20good_3bad", 20, 3),
    ("30good_5bad", 30, 5),
]
sim_users = [(label, *make_sim_user(ng, nb)) for label, ng, nb in user_profiles]

for label, liked, fb in sim_users:
    print(f"\n  [{label}]")
    t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked, fb)
    t_full = time.perf_counter() - t0
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
    print(f"  Full: {t_full:.1f}s")

    for sq_n, pb_n in [(300, 300), (500, 500), (500, 1000), (500, 1500)]:
        cands, elapsed = stage1_hybrid(liked, fb, sq_n, pb_n)
        recall = len(gt & set(cands)) / len(gt)
        print(f"    hybrid(sq={sq_n},pb={pb_n}) → {len(cands)} cands: "
              f"recall={recall:.0%}  {elapsed*1000:.0f}ms")


# ===================================================================
# 2. Padded 3D Tensor Reason Scoring
# ===================================================================
print("\n" + "=" * 65)
print("2. Padded 3D Tensor Reason Scoring")
print("=" * 65)


def batch_score_padded(index_obj, liked_books, fb_data, candidate_ids):
    """3D 텐서 패딩으로 reason 루프 최소화."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    cand_books = [(cid, index_obj.get_book(cid)) for cid in candidate_ids]
    cand_books = [(cid, bv) for cid, bv in cand_books if bv is not None]
    if not cand_books:
        return {}

    n_cands = len(cand_books)

    # desc/l1/l2/fb_desc — 행렬 연산 (v2와 동일)
    cand_descs = np.stack([bv.desc.astype(np.float32) for _, bv in cand_books])
    cand_l1s = np.stack([bv.l1.astype(np.float32) for _, bv in cand_books])
    cand_l2s = np.stack([bv.l2.astype(np.float32) for _, bv in cand_books])

    good_bvs = [(bid, index_obj.get_book(bid)) for bid in good_ids]
    good_bvs = [(bid, bv) for bid, bv in good_bvs if bv is not None]

    # desc_score
    if good_bvs:
        gd = np.stack([bv.desc.astype(np.float32) for _, bv in good_bvs])
        desc_scores = (cand_descs @ gd.T).max(axis=1)
    else:
        desc_scores = np.zeros(n_cands)

    # l1/l2
    if good_bvs:
        gl1 = np.stack([bv.l1.astype(np.float32) for _, bv in good_bvs])
        gl2 = np.stack([bv.l2.astype(np.float32) for _, bv in good_bvs])
        l1_scores = (cand_l1s @ gl1.T).max(axis=1)
        l2_scores = (cand_l2s @ gl2.T).max(axis=1)
    else:
        l1_scores = np.zeros(n_cands)
        l2_scores = np.zeros(n_cands)

    # fb_desc
    fb_entries = [(bid, fb) for bid, fb in fb_data.items()
                  if liked_books.get(bid, {}).get("rating") != "neutral"]
    if fb_entries:
        fb_vals = np.zeros((n_cands, len(fb_entries)))
        for j, (bid, fb) in enumerate(fb_entries):
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_vals[:, j] = sign * (cand_descs @ fb["emb"].astype(np.float32))
        fb_desc_scores = fb_vals.mean(axis=1)
    else:
        fb_desc_scores = np.zeros(n_cands)

    # --- reason_score: 3D 패딩 접근 ---
    # 후보 reasons을 패딩된 3D 텐서로 구축
    cand_reason_counts = [len(bv.reasons) for _, bv in cand_books]
    max_cand_r = max(cand_reason_counts) if cand_reason_counts else 0

    if max_cand_r == 0:
        reason_scores = np.zeros(n_cands)
    else:
        # (n_cands, max_cand_r, dim) — 패딩
        cand_reasons_3d = np.zeros((n_cands, max_cand_r, DIM), dtype=np.float32)
        cand_reason_mask = np.zeros((n_cands, max_cand_r), dtype=bool)  # True = valid
        for i, (_, bv) in enumerate(cand_books):
            for j, r in enumerate(bv.reasons):
                cand_reasons_3d[i, j] = r.astype(np.float32)
                cand_reason_mask[i, j] = True

        # good books의 reason도 패딩
        good_reason_data = []
        for bid, bv in good_bvs:
            fb = fb_data.get(bid)
            has_pos_fb = fb is not None and not fb["is_dislike"]
            good_reason_data.append((bv, fb if has_pos_fb else None))

        bad_bvs = [(bid, index_obj.get_book(bid)) for bid in bad_ids]
        bad_bvs = [(bid, bv) for bid, bv in bad_bvs if bv is not None]
        bad_reason_data = []
        for bid, bv in bad_bvs:
            fb = fb_data.get(bid)
            has_neg_fb = fb is not None and fb["is_dislike"]
            bad_reason_data.append((bv, fb if has_neg_fb else None))

        # 각 good/bad book에 대해 reason maxsim을 배치로 계산
        all_weighted = []

        for bv, fb in good_reason_data:
            if not bv.reasons:
                all_weighted.append(np.zeros(n_cands))
                continue

            # query reasons: (n_q, dim)
            q = np.stack(bv.reasons).astype(np.float32)
            n_q = q.shape[0]

            # 배치 matmul: (n_cands, max_cand_r, dim) @ (dim, n_q) → (n_cands, max_cand_r, n_q)
            sims = np.einsum('ijk,lk->ijl', cand_reasons_3d, q)  # (n_cands, max_cand_r, n_q)

            # 마스킹: 패딩된 위치의 유사도를 -inf로
            mask_expanded = cand_reason_mask[:, :, np.newaxis]  # (n_cands, max_cand_r, 1)
            sims = np.where(mask_expanded, sims, -np.inf)

            # maxsim: 각 query reason에 대해 candidate reasons 중 max → mean
            # sims shape: (n_cands, max_cand_r, n_q)
            max_per_query = sims.max(axis=1)  # (n_cands, n_q) — max over cand reasons
            # 모든 reason이 패딩인 후보는 -inf → 0으로 처리
            max_per_query = np.where(np.isinf(max_per_query), 0.0, max_per_query)
            r_sim = max_per_query.mean(axis=1)  # (n_cands,) — mean over query reasons

            if fb:
                # fb_sim: fb["emb"] vs 각 candidate의 reasons → max
                fb_emb = fb["emb"].astype(np.float32)
                fb_sims = cand_reasons_3d @ fb_emb  # (n_cands, max_cand_r)
                fb_sims = np.where(cand_reason_mask, fb_sims, -np.inf)
                fb_sim = fb_sims.max(axis=1)  # (n_cands,)
                fb_sim = np.where(np.isinf(fb_sim), 0.0, fb_sim)
                w = FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim
            else:
                w = REASON_WEIGHT_WITHOUT_FB * r_sim

            all_weighted.append(w)

        for bv, fb in bad_reason_data:
            if not bv.reasons:
                all_weighted.append(np.zeros(n_cands))
                continue

            q = np.stack(bv.reasons).astype(np.float32)
            sims = np.einsum('ijk,lk->ijl', cand_reasons_3d, q)
            mask_expanded = cand_reason_mask[:, :, np.newaxis]
            sims = np.where(mask_expanded, sims, -np.inf)
            max_per_query = sims.max(axis=1)
            max_per_query = np.where(np.isinf(max_per_query), 0.0, max_per_query)
            r_sim = max_per_query.mean(axis=1)

            if fb:
                fb_emb = fb["emb"].astype(np.float32)
                fb_sims = cand_reasons_3d @ fb_emb
                fb_sims = np.where(cand_reason_mask, fb_sims, -np.inf)
                fb_sim = fb_sims.max(axis=1)
                fb_sim = np.where(np.isinf(fb_sim), 0.0, fb_sim)
                w = -(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                w = -(REASON_WEIGHT_WITHOUT_FB * r_sim)

            all_weighted.append(w)

        if all_weighted:
            reason_scores = np.mean(np.stack(all_weighted), axis=0)  # (n_cands,)
        else:
            reason_scores = np.zeros(n_cands)

    final = (W_REASON * reason_scores + W_DESC * desc_scores +
             W_L1 * l1_scores + W_L2 * l2_scores + W_FB_DESC * fb_desc_scores)
    return {cid: float(final[i]) for i, (cid, _) in enumerate(cand_books)}


# 정확성 검증
print("\n정확성 검증 (padded 3D vs loop)...")
test_liked, test_fb = sim_users[0][1], sim_users[0][2]
test_cands = [bid for bid in all_bids[:200] if bid not in test_liked]

active = {bid: d for bid, d in test_liked.items() if d["rating"] != "neutral"}
loop_scores = {cid: _score_one(index, active, test_fb, cid) for cid in test_cands}
padded_scores = batch_score_padded(index, test_liked, test_fb, test_cands)

diffs = [abs(loop_scores[cid] - padded_scores.get(cid, 0)) for cid in test_cands]
max_diff = max(diffs)
mean_diff = np.mean(diffs)
print(f"  max diff: {max_diff:.6f}, mean diff: {mean_diff:.6f}")
print(f"  정확성: {'PASS' if max_diff < 0.01 else 'FAIL'}")

# 순위 일치 확인
loop_ranking = sorted(test_cands, key=lambda x: loop_scores[x], reverse=True)[:20]
padded_ranking = sorted(test_cands, key=lambda x: padded_scores.get(x, 0), reverse=True)[:20]
rank_overlap = len(set(loop_ranking) & set(padded_ranking))
print(f"  top-20 순위 일치: {rank_overlap}/20")


# 속도 비교: v2 batch vs v3 padded 3D
print("\n속도 비교 (v2 batch loop vs v3 padded 3D)...")

# v2 방식 (비교용)
def batch_score_v2(index_obj, liked_books, fb_data, candidate_ids):
    """v2의 reason 루프 방식 (비교용)."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    cand_books = [(cid, index_obj.get_book(cid)) for cid in candidate_ids]
    cand_books = [(cid, bv) for cid, bv in cand_books if bv is not None]
    if not cand_books:
        return {}
    n_cands = len(cand_books)
    cand_descs = np.stack([bv.desc.astype(np.float32) for _, bv in cand_books])
    cand_l1s = np.stack([bv.l1.astype(np.float32) for _, bv in cand_books])
    cand_l2s = np.stack([bv.l2.astype(np.float32) for _, bv in cand_books])
    good_bvs = [(bid, index_obj.get_book(bid)) for bid in good_ids]
    good_bvs = [(bid, bv) for bid, bv in good_bvs if bv is not None]
    if good_bvs:
        gd = np.stack([bv.desc.astype(np.float32) for _, bv in good_bvs])
        desc_scores = (cand_descs @ gd.T).max(axis=1)
        gl1 = np.stack([bv.l1.astype(np.float32) for _, bv in good_bvs])
        gl2 = np.stack([bv.l2.astype(np.float32) for _, bv in good_bvs])
        l1_scores = (cand_l1s @ gl1.T).max(axis=1)
        l2_scores = (cand_l2s @ gl2.T).max(axis=1)
    else:
        desc_scores = l1_scores = l2_scores = np.zeros(n_cands)
    fb_entries = [(bid, fb) for bid, fb in fb_data.items()
                  if liked_books.get(bid, {}).get("rating") != "neutral"]
    if fb_entries:
        fb_vals = np.zeros((n_cands, len(fb_entries)))
        for j, (bid, fb) in enumerate(fb_entries):
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_vals[:, j] = sign * (cand_descs @ fb["emb"].astype(np.float32))
        fb_desc_scores = fb_vals.mean(axis=1)
    else:
        fb_desc_scores = np.zeros(n_cands)
    reason_scores = np.zeros(n_cands)
    good_data = []
    for bid, bv in good_bvs:
        fb = fb_data.get(bid)
        good_data.append((bv, fb if fb and not fb["is_dislike"] else None))
    bad_bvs2 = [(bid, index_obj.get_book(bid)) for bid in bad_ids]
    bad_data = []
    for bid, bv in bad_bvs2:
        if bv is None: continue
        fb = fb_data.get(bid)
        bad_data.append((bv, fb if fb and fb["is_dislike"] else None))
    for i, (cid, cand_bv) in enumerate(cand_books):
        if not cand_bv.reasons: continue
        cand_r = np.stack(cand_bv.reasons).astype(np.float32)
        weighted = []
        for bv, fb in good_data:
            if fb:
                fb_sim = float((cand_r @ fb["emb"].astype(np.float32)).max())
                r_sim = float((np.stack(bv.reasons).astype(np.float32) @ cand_r.T).max(axis=1).mean()) if bv.reasons else 0.0
                weighted.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                r_sim = float((np.stack(bv.reasons).astype(np.float32) @ cand_r.T).max(axis=1).mean()) if bv.reasons else 0.0
                weighted.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
        for bv, fb in bad_data:
            if fb:
                fb_sim = float((cand_r @ fb["emb"].astype(np.float32)).max())
                r_sim = float((np.stack(bv.reasons).astype(np.float32) @ cand_r.T).max(axis=1).mean()) if bv.reasons else 0.0
                weighted.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                r_sim = float((np.stack(bv.reasons).astype(np.float32) @ cand_r.T).max(axis=1).mean()) if bv.reasons else 0.0
                weighted.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
        if weighted:
            reason_scores[i] = float(np.mean(weighted))
    final = (W_REASON * reason_scores + W_DESC * desc_scores +
             W_L1 * l1_scores + W_L2 * l2_scores + W_FB_DESC * fb_desc_scores)
    return {cid: float(final[i]) for i, (cid, _) in enumerate(cand_books)}


for label, liked, fb in sim_users:
    n_good = sum(1 for d in liked.values() if d["rating"] == "good")
    for cand_n in [200, 500, 1000]:
        cands_h, _ = stage1_hybrid(liked, fb, cand_n, cand_n)
        cands_h = cands_h[:cand_n]

        t0 = time.perf_counter()
        batch_score_v2(index, liked, fb, cands_h)
        t_v2 = time.perf_counter() - t0

        t0 = time.perf_counter()
        batch_score_padded(index, liked, fb, cands_h)
        t_v3 = time.perf_counter() - t0

        speedup = t_v2 / t_v3 if t_v3 > 0 else 0
        print(f"  [{label}] {cand_n} cands: v2={t_v2*1000:.0f}ms  v3={t_v3*1000:.0f}ms  "
              f"speedup={speedup:.1f}x")


# ===================================================================
# 3. 최적 후보 수 탐색 (recall vs latency 곡선)
# ===================================================================
print("\n" + "=" * 65)
print("3. Recall vs Latency 곡선 (최적 후보 수 탐색)")
print("=" * 65)

CAND_SIZES = [100, 150, 200, 250, 300, 400, 500]

for label, liked, fb in sim_users:
    print(f"\n  [{label}]")
    full_scores = recommend_scores(index, liked, fb)
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K])

    for cn in CAND_SIZES:
        cands, t_s1 = stage1_hybrid(liked, fb, cn, cn)
        cands = cands[:cn]
        t0 = time.perf_counter()
        s2 = batch_score_padded(index, liked, fb, cands)
        t_s2 = time.perf_counter() - t0
        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
        recall = len(gt & top20) / len(gt)
        total_ms = t_s1 * 1000 + t_s2 * 1000
        marker = " ←" if recall >= 0.95 and total_ms < 200 else (" ?" if recall >= 0.90 else "")
        print(f"    {cn:>4} cands: recall={recall:.0%}  "
              f"S1={t_s1*1000:.0f}ms S2={t_s2*1000:.0f}ms total={total_ms:.0f}ms{marker}")


# ===================================================================
# 4. 리스크 검증
# ===================================================================
print("\n" + "=" * 65)
print("4. 리스크 검증")
print("=" * 65)

# 4a. Reason 분포 편향 — 평균벡터가 개별 reason과 얼마나 다른가
print("\n4a. Reason 평균벡터 왜곡 분석")
agg_vs_individual = []
for bid in all_bids:
    bv = index.get_book(bid)
    if len(bv.reasons) < 2:
        continue
    agg = agg_reason_vecs[bid].astype(np.float32)
    sims = [float(np.dot(agg, r.astype(np.float32))) for r in bv.reasons]
    min_sim = min(sims)
    mean_sim = np.mean(sims)
    agg_vs_individual.append((bid, min_sim, mean_sim, len(bv.reasons)))

agg_vs_individual.sort(key=lambda x: x[1])
print(f"  books with 2+ reasons: {len(agg_vs_individual)}")
min_sims = [x[1] for x in agg_vs_individual]
print(f"  평균벡터 vs 개별reason 최소유사도:")
print(f"    p10={np.percentile(min_sims, 10):.3f}  p25={np.percentile(min_sims, 25):.3f}  "
      f"median={np.median(min_sims):.3f}  p75={np.percentile(min_sims, 75):.3f}")
low_sim_count = sum(1 for s in min_sims if s < 0.3)
print(f"  min_sim < 0.3 (심각한 왜곡): {low_sim_count}권 ({low_sim_count/len(agg_vs_individual)*100:.1f}%)")
low_sim_05 = sum(1 for s in min_sims if s < 0.5)
print(f"  min_sim < 0.5 (주의): {low_sim_05}권 ({low_sim_05/len(agg_vs_individual)*100:.1f}%)")

# 가장 왜곡 심한 책 예시
print(f"\n  가장 왜곡 심한 5권:")
for bid, min_s, mean_s, rc in agg_vs_individual[:5]:
    title = books_meta.get(bid, {}).get("title", "?")[:30]
    print(f"    {title}: min_sim={min_s:.3f} mean_sim={mean_s:.3f} reasons={rc}")

# 4b. Reason 없는 책 처리
print("\n4b. Reason 없는 책")
no_reason = [bid for bid in all_bids if len(index.get_book(bid).reasons) == 0]
print(f"  reason 0개: {len(no_reason)}권 ({len(no_reason)/N*100:.1f}%)")
few_reason = [bid for bid in all_bids if 0 < len(index.get_book(bid).reasons) <= 2]
print(f"  reason 1~2개: {len(few_reason)}권 ({len(few_reason)/N*100:.1f}%)")

# 4c. 유저 취향 다양성 vs recall
print("\n4c. 취향 다양성 vs recall")
print("  (좋아요 책들 간 desc 유사도 평균 = 취향 집중도)")

DIVERSITY_TESTS = 20  # 랜덤 유저 수
diversity_results = []
for _ in range(DIVERSITY_TESTS):
    liked, fb = make_sim_user(10, 2)
    good_ids = [bid for bid, d in liked.items() if d["rating"] == "good"]
    good_descs_list = [index.get_book(bid).desc.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if len(good_descs_list) < 2:
        continue

    # 취향 집중도: 좋아요 책들 간 평균 cosine
    gm = np.stack(good_descs_list)
    pairwise = gm @ gm.T
    n = len(gm)
    upper_tri = [pairwise[i, j] for i in range(n) for j in range(i + 1, n)]
    concentration = float(np.mean(upper_tri))

    full_scores = recommend_scores(index, liked, fb)
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K])

    cands, _ = stage1_hybrid(liked, fb, 300, 300)
    cands = cands[:300]
    s2 = batch_score_padded(index, liked, fb, cands)
    top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
    recall = len(gt & top20) / len(gt)

    diversity_results.append((concentration, recall))

diversity_results.sort(key=lambda x: x[0])
print(f"  {len(diversity_results)} 랜덤 유저 테스트:")
low_conc = [r for c, r in diversity_results if c < 0.3]
mid_conc = [r for c, r in diversity_results if 0.3 <= c < 0.5]
high_conc = [r for c, r in diversity_results if c >= 0.5]
if low_conc:
    print(f"    다양한 취향 (conc<0.3): avg recall={np.mean(low_conc):.0%} ({len(low_conc)}명)")
if mid_conc:
    print(f"    보통 취향 (0.3≤conc<0.5): avg recall={np.mean(mid_conc):.0%} ({len(mid_conc)}명)")
if high_conc:
    print(f"    집중된 취향 (conc≥0.5): avg recall={np.mean(high_conc):.0%} ({len(high_conc)}명)")

# 4d. 5만권 스케일 시뮬레이션 (실측 기반 외삽)
print("\n4d. 스케일 시뮬레이션 (실측 기반)")
# Stage 1: O(N × dim) 행렬곱 — 현재 N에서 실측 후 선형 외삽
# Stage 2: 후보 수 고정이므로 상수

# Stage 1 실측
liked_10, fb_10 = sim_users[1][1], sim_users[1][2]
times_s1 = []
for _ in range(5):
    t0 = time.perf_counter()
    stage1_hybrid(liked_10, fb_10, 300, 300)
    times_s1.append(time.perf_counter() - t0)
s1_base = np.median(times_s1) * 1000  # ms

# Stage 2 실측 (300 cands)
cands_300, _ = stage1_hybrid(liked_10, fb_10, 300, 300)
cands_300 = cands_300[:300]
times_s2 = []
for _ in range(3):
    t0 = time.perf_counter()
    batch_score_padded(index, liked_10, fb_10, cands_300)
    times_s2.append(time.perf_counter() - t0)
s2_base = np.median(times_s2) * 1000  # ms

print(f"  기준 실측 (N={N}, 10good, 300 cands):")
print(f"    Stage 1: {s1_base:.0f}ms")
print(f"    Stage 2: {s2_base:.0f}ms")
print(f"    합계:    {s1_base + s2_base:.0f}ms")

for target_n in [5000, 10000, 30000, 50000, 100000]:
    scale = target_n / N
    s1_est = s1_base * scale
    s2_est = s2_base  # 후보 수 고정이므로 상수
    mem_s1 = target_n * DIM * 2 * 2 / 1024 / 1024  # desc + agg_reason, float16
    mem_s2_per_req = 300 * max_reasons * DIM * 4 / 1024 / 1024  # 300 후보의 reason, float32
    total = s1_est + s2_est
    print(f"\n  {target_n:>6,}권: S1={s1_est:.0f}ms + S2={s2_est:.0f}ms = {total:.0f}ms  "
          f"상주메모리={mem_s1:.0f}MB  요청당={mem_s2_per_req:.0f}MB")
    if total < 200:
        print(f"    → 200ms 목표 충족 ✓")
    else:
        print(f"    → 200ms 초과 ✗ (Stage 1 최적화 필요)")

print("\n" + "=" * 65)
print("벤치마크 완료")
print("=" * 65)
