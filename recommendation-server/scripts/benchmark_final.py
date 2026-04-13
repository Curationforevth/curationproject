#!/usr/bin/env python3
"""최종 벤치마크 — 설계 확정 전 모든 검증 가능한 항목.

검증:
  1. Float16 prestacked — 메모리 절반, 정확성
  2. H10_no_l1 가중치 — 프로덕션 가중치로 recall/latency
  3. 후보 수 700~1000 — recall min 개선
  4. 현실적 유저 시뮬레이션 — 장르 클러스터 유저
  5. cap_dynamic 시뮬레이션 — L1 분포 재조정
  6. Supabase egress 추정
  7. pickle 로드 + startup 시간

사용법: cd recommendation-server && python3 -u scripts/benchmark_final.py
"""
from __future__ import annotations

import os
import sys
import time
import pickle
import tempfile
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from engine.index import VectorIndex, BookVectors
from engine.loader import load_index
from engine.scorer import _score_one, _maxsim
from config import (REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB, FB_REASON_WEIGHT)

pkl_path = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
print("=" * 70)
print("최종 벤치마크 — 설계 확정 전 전수 검증")
print("=" * 70)

index, books_meta, built_at = load_index(pkl_path)
all_bids = index.book_ids
N = len(all_bids)
DIM = index.dim
print(f"  books: {N}, dim: {DIM}, built_at: {built_at}")

rng = np.random.default_rng(42)

# ===================================================================
# 공통 유틸
# ===================================================================

# 사전 계산
agg_reason_vecs_f32 = {}
agg_reason_vecs_f16 = {}
prestacked_f32 = {}
prestacked_f16 = {}
book_l1_ids = {}  # bid → l1_genre_id (cap_dynamic용)

for bid in all_bids:
    bv = index.get_book(bid)
    if bv.reasons:
        stacked = np.stack(bv.reasons)
        prestacked_f32[bid] = stacked.astype(np.float32)
        prestacked_f16[bid] = stacked.astype(np.float16)
        mean_vec = stacked.astype(np.float32).mean(axis=0)
        norm = np.linalg.norm(mean_vec)
        normed = (mean_vec / norm) if norm > 0 else mean_vec
        agg_reason_vecs_f32[bid] = normed.astype(np.float32)
        agg_reason_vecs_f16[bid] = normed.astype(np.float16)
    else:
        prestacked_f32[bid] = np.empty((0, DIM), dtype=np.float32)
        prestacked_f16[bid] = np.empty((0, DIM), dtype=np.float16)
        agg_reason_vecs_f32[bid] = np.zeros(DIM, dtype=np.float32)
        agg_reason_vecs_f16[bid] = np.zeros(DIM, dtype=np.float16)

bid_order = list(all_bids)
bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}
desc_matrix_f32 = np.stack([index.get_book(bid).desc.astype(np.float32) for bid in bid_order])
desc_matrix_f16 = desc_matrix_f32.astype(np.float16)
agg_matrix_f32 = np.stack([agg_reason_vecs_f32[bid] for bid in bid_order])
agg_matrix_f16 = np.stack([agg_reason_vecs_f16[bid] for bid in bid_order])

# L1 벡터 → 클러스터 ID (가장 가까운 유니크 L1 찾기)
l1_vecs = {}
for bid in all_bids:
    bv = index.get_book(bid)
    l1_key = tuple(bv.l1.astype(np.float16).tolist()[:10])  # 앞 10차원으로 클러스터 구분
    l1_vecs[bid] = l1_key

# 유니크 L1 클러스터
unique_l1s = list(set(l1_vecs.values()))
l1_to_cluster = {l1: i for i, l1 in enumerate(unique_l1s)}
book_cluster = {bid: l1_to_cluster[l1_vecs[bid]] for bid in all_bids}
cluster_books = {}
for bid, cl in book_cluster.items():
    cluster_books.setdefault(cl, []).append(bid)
print(f"  L1 클러스터 수: {len(unique_l1s)}")
print(f"  클러스터 크기 분포: min={min(len(v) for v in cluster_books.values())}, "
      f"max={max(len(v) for v in cluster_books.values())}, "
      f"avg={np.mean([len(v) for v in cluster_books.values()]):.0f}")


def stage1_hybrid(liked_books, fb_data, top_n, use_f16=False):
    dm = desc_matrix_f16 if use_f16 else desc_matrix_f32
    am = agg_matrix_f16 if use_f16 else agg_matrix_f32
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())

    dtype = np.float16 if use_f16 else np.float32

    good_descs = [index.get_book(bid).desc.astype(dtype) for bid in good_ids if index.get_book(bid)]
    if not good_descs:
        return []
    gd = np.stack(good_descs)
    sq_desc = (dm.astype(np.float32) @ gd.astype(np.float32).T).max(axis=1)

    avecs = agg_reason_vecs_f16 if use_f16 else agg_reason_vecs_f32
    ga = [avecs[bid].astype(np.float32) for bid in good_ids if bid in avecs]
    sq_reason = (am.astype(np.float32) @ np.stack(ga).T).max(axis=1) if ga else np.zeros(N)

    sq_fb = np.zeros(N, dtype=np.float32)
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        sq_fb += sign * (dm.astype(np.float32) @ fb["emb"].astype(np.float32))

    sq_scores = 3.0 * sq_desc + 2.0 * sq_reason + 2.0 * sq_fb

    pb_scores = np.zeros(N, dtype=np.float32)
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None: continue
        pb_scores += 3.0 * (dm.astype(np.float32) @ bv.desc.astype(np.float32))
        av = avecs.get(bid)
        if av is not None:
            pb_scores += 2.0 * (am.astype(np.float32) @ av.astype(np.float32))
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None: continue
        pb_scores -= 1.5 * (dm.astype(np.float32) @ bv.desc.astype(np.float32))
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral": continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        pb_scores += sign * 2.0 * (dm.astype(np.float32) @ fb["emb"].astype(np.float32))

    sq_v = sq_scores[sq_scores > -900]
    pb_v = pb_scores[pb_scores > -900]
    sq_n = (sq_scores - sq_v.min()) / (sq_v.max() - sq_v.min() + 1e-9) if len(sq_v) > 1 else np.zeros_like(sq_scores)
    pb_n = (pb_scores - pb_v.min()) / (pb_v.max() - pb_v.min() + 1e-9) if len(pb_v) > 1 else np.zeros_like(pb_scores)
    combined = sq_n + pb_n
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            combined[idx] = -999.0
    top_idx = np.argsort(combined)[::-1][:top_n]
    return [bid_order[i] for i in top_idx]


def batch_score(index_obj, liked_books, fb_data, candidate_ids,
                w_reason=1.0, w_desc=0.5, w_l1=3.0, w_l2=1.0, w_fb_desc=2.0,
                use_f16_reasons=False):
    """배치 스코어링. 가중치 파라미터화."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    cand_books = [(cid, index_obj.get_book(cid)) for cid in candidate_ids]
    cand_books = [(cid, bv) for cid, bv in cand_books if bv is not None]
    if not cand_books:
        return {}
    n_cands = len(cand_books)

    cand_descs = np.stack([bv.desc.astype(np.float32) for _, bv in cand_books])
    good_bvs = [(bid, index_obj.get_book(bid)) for bid in good_ids]
    good_bvs = [(bid, bv) for bid, bv in good_bvs if bv is not None]

    if good_bvs:
        gd = np.stack([bv.desc.astype(np.float32) for _, bv in good_bvs])
        desc_scores = (cand_descs @ gd.T).max(axis=1)
    else:
        desc_scores = np.zeros(n_cands)

    if w_l1 != 0 and good_bvs:
        cand_l1s = np.stack([bv.l1.astype(np.float32) for _, bv in cand_books])
        gl1 = np.stack([bv.l1.astype(np.float32) for _, bv in good_bvs])
        l1_scores = (cand_l1s @ gl1.T).max(axis=1)
    else:
        l1_scores = np.zeros(n_cands)

    if w_l2 != 0 and good_bvs:
        cand_l2s = np.stack([bv.l2.astype(np.float32) for _, bv in cand_books])
        gl2 = np.stack([bv.l2.astype(np.float32) for _, bv in good_bvs])
        l2_scores = (cand_l2s @ gl2.T).max(axis=1)
    else:
        l2_scores = np.zeros(n_cands)

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

    # reason scoring
    reason_scores = np.zeros(n_cands)
    ps = prestacked_f16 if use_f16_reasons else prestacked_f32

    good_data = []
    for bid, bv in good_bvs:
        fb = fb_data.get(bid)
        good_data.append((bid, fb if fb and not fb["is_dislike"] else None))
    bad_data = []
    for bid in bad_ids:
        bv = index_obj.get_book(bid)
        if bv is None: continue
        fb = fb_data.get(bid)
        bad_data.append((bid, fb if fb and fb["is_dislike"] else None))

    for i, (cid, _) in enumerate(cand_books):
        cand_r = ps[cid].astype(np.float32)
        if cand_r.shape[0] == 0: continue
        weighted = []
        for bid, fb in good_data:
            query_r = ps[bid].astype(np.float32)
            if fb:
                fb_sim = float((cand_r @ fb["emb"]).max())
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
        for bid, fb in bad_data:
            query_r = ps[bid].astype(np.float32)
            if fb:
                fb_sim = float((cand_r @ fb["emb"]).max())
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
        if weighted:
            reason_scores[i] = float(np.mean(weighted))

    final = (w_reason * reason_scores + w_desc * desc_scores +
             w_l1 * l1_scores + w_l2 * l2_scores + w_fb_desc * fb_desc_scores)
    return {cid: float(final[i]) for i, (cid, _) in enumerate(cand_books)}


def full_scoring(index_obj, liked_books, fb_data,
                 w_reason=1.0, w_desc=0.5, w_l1=3.0, w_l2=1.0, w_fb_desc=2.0):
    """전체 brute-force scoring (ground truth)."""
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    if not active:
        return {}
    read_ids = set(liked_books.keys())
    good_ids = [bid for bid, d in active.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in active.items() if d["rating"] == "bad"]
    scores = {}
    for cid in index_obj.book_ids:
        if cid in read_ids:
            continue
        cand = index_obj.get_book(cid)
        if cand is None:
            continue
        # reason
        weighted_maxsims = []
        for bid in good_ids:
            bv = index_obj.get_book(bid)
            if bv is None: continue
            fb = fb_data.get(bid)
            if fb and not fb["is_dislike"]:
                fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
                r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
                weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
                weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
        for bid in bad_ids:
            bv = index_obj.get_book(bid)
            if bv is None: continue
            fb = fb_data.get(bid)
            if fb and fb["is_dislike"]:
                fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
                r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
                weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
                weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
        reason_score = float(np.mean(weighted_maxsims)) if weighted_maxsims else 0.0
        good_descs = [index_obj.get_book(bid).desc for bid in good_ids if index_obj.get_book(bid)]
        desc_score = max(float(np.dot(d, cand.desc)) for d in good_descs) if good_descs else 0.0
        good_l1s = [index_obj.get_book(bid).l1 for bid in good_ids if index_obj.get_book(bid)]
        l1_score = max(float(np.dot(l, cand.l1)) for l in good_l1s) if good_l1s else 0.0
        good_l2s = [index_obj.get_book(bid).l2 for bid in good_ids if index_obj.get_book(bid)]
        l2_score = max(float(np.dot(l, cand.l2)) for l in good_l2s) if good_l2s else 0.0
        fb_desc_vals = []
        for bid, fb in fb_data.items():
            if liked_books.get(bid, {}).get("rating") == "neutral": continue
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_desc_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
        fb_desc_score = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0
        scores[cid] = (w_reason * reason_score + w_desc * desc_score +
                       w_l1 * l1_score + w_l2 * l2_score + w_fb_desc * fb_desc_score)
    return scores


def make_random_user(n_good, n_bad=0):
    chosen = rng.choice(all_bids, size=n_good + n_bad, replace=False)
    liked, fb = {}, {}
    for i, bid in enumerate(chosen):
        if i < n_good:
            liked[bid] = {"rating": "good"}
            bv = index.get_book(bid)
            if bv and bv.reasons and rng.random() > 0.5:
                fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": False}
        else:
            liked[bid] = {"rating": "bad"}
            bv = index.get_book(bid)
            if bv and bv.reasons:
                fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": True}
    return liked, fb


def make_clustered_user(n_good, n_clusters, n_bad=0):
    """장르가 클러스터링된 현실적 유저. n_clusters개 장르에서 좋아요 분배."""
    chosen_clusters = rng.choice(list(cluster_books.keys()), size=min(n_clusters, len(cluster_books)), replace=False)
    per_cluster = max(1, n_good // len(chosen_clusters))
    good_bids = []
    for cl in chosen_clusters:
        available = cluster_books[cl]
        pick_n = min(per_cluster, len(available))
        good_bids.extend(rng.choice(available, size=pick_n, replace=False).tolist())
    good_bids = good_bids[:n_good]

    # bad는 다른 클러스터에서
    other_bids = [bid for bid in all_bids if bid not in good_bids]
    bad_bids = rng.choice(other_bids, size=min(n_bad, len(other_bids)), replace=False).tolist() if n_bad > 0 else []

    liked, fb = {}, {}
    for bid in good_bids:
        liked[bid] = {"rating": "good"}
        bv = index.get_book(bid)
        if bv and bv.reasons and rng.random() > 0.5:
            fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": False}
    for bid in bad_bids:
        liked[bid] = {"rating": "bad"}
        bv = index.get_book(bid)
        if bv and bv.reasons:
            fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": True}
    return liked, fb


# ===================================================================
# 1. Float16 prestacked — 정확성 + 메모리
# ===================================================================
print("\n" + "=" * 70)
print("1. Float16 prestacked 검증")
print("=" * 70)

mem_f32 = sum(a.nbytes for a in prestacked_f32.values()) / 1024 / 1024
mem_f16 = sum(a.nbytes for a in prestacked_f16.values()) / 1024 / 1024
print(f"  prestacked f32: {mem_f32:.1f}MB")
print(f"  prestacked f16: {mem_f16:.1f}MB")
print(f"  절감: {(1 - mem_f16/mem_f32)*100:.0f}%")

# 정확성: f32 batch vs f16 batch
test_liked, test_fb = make_random_user(10, 2)
test_cands = stage1_hybrid(test_liked, test_fb, 200)

scores_f32 = batch_score(index, test_liked, test_fb, test_cands, use_f16_reasons=False)
scores_f16 = batch_score(index, test_liked, test_fb, test_cands, use_f16_reasons=True)

diffs = [abs(scores_f32[c] - scores_f16.get(c, 0)) for c in test_cands if c in scores_f32]
max_diff = max(diffs)
mean_diff = np.mean(diffs)

# 순위 일치
rank_f32 = sorted(scores_f32.items(), key=lambda x: x[1], reverse=True)[:20]
rank_f16 = sorted(scores_f16.items(), key=lambda x: x[1], reverse=True)[:20]
rank_overlap = len(set(b for b, _ in rank_f32) & set(b for b, _ in rank_f16))

print(f"  max score diff: {max_diff:.6f}")
print(f"  mean score diff: {mean_diff:.6f}")
print(f"  top-20 순위 일치: {rank_overlap}/20")
print(f"  정확성: {'PASS' if max_diff < 0.05 else 'FAIL'}")

# f16 속도
for cn in [300, 500, 700]:
    cands = stage1_hybrid(test_liked, test_fb, cn)
    t0 = time.perf_counter()
    batch_score(index, test_liked, test_fb, cands, use_f16_reasons=False)
    t_f32 = time.perf_counter() - t0
    t0 = time.perf_counter()
    batch_score(index, test_liked, test_fb, cands, use_f16_reasons=True)
    t_f16 = time.perf_counter() - t0
    print(f"  {cn} cands: f32={t_f32*1000:.0f}ms  f16={t_f16*1000:.0f}ms")


# ===================================================================
# 2. H10_no_l1 가중치 (W_R=2, W_D=3, W_L1=0, W_L2=0, W_FB=2)
# ===================================================================
print("\n" + "=" * 70)
print("2. H10_no_l1 가중치 검증")
print("=" * 70)

H10 = {"w_reason": 2.0, "w_desc": 3.0, "w_l1": 0.0, "w_l2": 0.0, "w_fb_desc": 2.0}
DEFAULT = {"w_reason": 1.0, "w_desc": 0.5, "w_l1": 3.0, "w_l2": 1.0, "w_fb_desc": 2.0}

profiles = [
    ("6good", 6, 0),
    ("10good_2bad", 10, 2),
    ("20good_3bad", 20, 3),
]
sim_users = [(label, *make_random_user(ng, nb)) for label, ng, nb in profiles]

for label, liked, fb in sim_users:
    print(f"\n  [{label}]")

    # Ground truth with H10 weights (full scoring)
    t0 = time.perf_counter()
    gt_h10 = full_scoring(index, liked, fb, **H10)
    t_full = time.perf_counter() - t0
    gt_top20 = set(bid for bid, _ in sorted(gt_h10.items(), key=lambda x: x[1], reverse=True)[:20])

    print(f"  H10 full scoring: {t_full:.1f}s")

    for cn in [300, 500, 700]:
        t0 = time.perf_counter()
        cands = stage1_hybrid(liked, fb, cn)
        t_s1 = time.perf_counter() - t0

        t0 = time.perf_counter()
        s2 = batch_score(index, liked, fb, cands, **H10, use_f16_reasons=True)
        t_s2 = time.perf_counter() - t0

        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:20])
        recall = len(gt_top20 & top20) / len(gt_top20)
        total = t_s1 + t_s2
        print(f"    {cn} cands: recall={recall:.0%}  S1={t_s1*1000:.0f}ms S2={t_s2*1000:.0f}ms "
              f"total={total*1000:.0f}ms")

    # H10 vs Default 비교 — L1/L2 제거로 Stage 2 속도 개선?
    cands_500 = stage1_hybrid(liked, fb, 500)
    t0 = time.perf_counter()
    batch_score(index, liked, fb, cands_500, **DEFAULT, use_f16_reasons=True)
    t_default = time.perf_counter() - t0
    t0 = time.perf_counter()
    batch_score(index, liked, fb, cands_500, **H10, use_f16_reasons=True)
    t_h10 = time.perf_counter() - t0
    print(f"    500c 속도: default={t_default*1000:.0f}ms  H10={t_h10*1000:.0f}ms  "
          f"개선={(1-t_h10/t_default)*100:.0f}%")


# ===================================================================
# 3. 후보 수 700~1000 + recall
# ===================================================================
print("\n" + "=" * 70)
print("3. 후보 수 확장 (700~1000) — 50명 스트레스 테스트")
print("=" * 70)

stress_rng = np.random.default_rng(777)
STRESS_N = 50
TOP_K = 20
CAND_SIZES = [300, 500, 700, 1000]

results = {cn: [] for cn in CAND_SIZES}
latencies = {cn: [] for cn in CAND_SIZES}

for i in range(STRESS_N):
    n_good = int(stress_rng.integers(6, 25))
    n_bad = int(stress_rng.integers(0, 5))
    liked, fb = make_random_user(n_good, n_bad)

    gt = full_scoring(index, liked, fb, **H10)
    gt_set = set(bid for bid, _ in sorted(gt.items(), key=lambda x: x[1], reverse=True)[:TOP_K])

    for cn in CAND_SIZES:
        t0 = time.perf_counter()
        cands = stage1_hybrid(liked, fb, cn)
        s2 = batch_score(index, liked, fb, cands, **H10, use_f16_reasons=True)
        elapsed = time.perf_counter() - t0

        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
        recall = len(gt_set & top20) / len(gt_set) if gt_set else 1.0
        results[cn].append(recall)
        latencies[cn].append(elapsed * 1000)

    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{STRESS_N} 완료...")

print(f"\n  결과 (H10 가중치, f16, {STRESS_N}명):")
for cn in CAND_SIZES:
    r = results[cn]
    l = latencies[cn]
    print(f"    {cn:>4} cands: recall avg={np.mean(r):.0%} median={np.median(r):.0%} "
          f"min={min(r):.0%} p5={np.percentile(r, 5):.0%} <90%={sum(1 for x in r if x < 0.9)}명  "
          f"latency avg={np.mean(l):.0f}ms p95={np.percentile(l, 95):.0f}ms")


# ===================================================================
# 4. 현실적 유저 시뮬레이션 — 장르 클러스터
# ===================================================================
print("\n" + "=" * 70)
print("4. 현실적 유저 (장르 클러스터) vs 랜덤 유저")
print("=" * 70)

CLUSTER_N = 30

clustered_results = {cn: [] for cn in [500, 700]}
clustered_latencies = {cn: [] for cn in [500, 700]}

for i in range(CLUSTER_N):
    n_good = int(rng.integers(6, 20))
    n_clusters = int(rng.integers(1, 4))  # 1~3개 장르
    liked, fb = make_clustered_user(n_good, n_clusters, n_bad=int(rng.integers(0, 3)))

    gt = full_scoring(index, liked, fb, **H10)
    gt_set = set(bid for bid, _ in sorted(gt.items(), key=lambda x: x[1], reverse=True)[:TOP_K])

    for cn in [500, 700]:
        t0 = time.perf_counter()
        cands = stage1_hybrid(liked, fb, cn)
        s2 = batch_score(index, liked, fb, cands, **H10, use_f16_reasons=True)
        elapsed = time.perf_counter() - t0

        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
        recall = len(gt_set & top20) / len(gt_set) if gt_set else 1.0
        clustered_results[cn].append(recall)
        clustered_latencies[cn].append(elapsed * 1000)

    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{CLUSTER_N} 완료...")

print(f"\n  장르 클러스터 유저 ({CLUSTER_N}명, 1~3 장르):")
for cn in [500, 700]:
    r = clustered_results[cn]
    l = clustered_latencies[cn]
    print(f"    {cn} cands: recall avg={np.mean(r):.0%} median={np.median(r):.0%} "
          f"min={min(r):.0%} <90%={sum(1 for x in r if x < 0.9)}명  "
          f"latency avg={np.mean(l):.0f}ms")

# 비교: 랜덤 유저 동일 조건
print(f"\n  (비교) 랜덤 유저:")
for cn in [500, 700]:
    r = results[cn]
    print(f"    {cn} cands: recall avg={np.mean(r):.0%} median={np.median(r):.0%} "
          f"min={min(r):.0%} <90%={sum(1 for x in r if x < 0.9)}명")


# ===================================================================
# 5. cap_dynamic 시뮬레이션
# ===================================================================
print("\n" + "=" * 70)
print("5. cap_dynamic 시뮬레이션")
print("=" * 70)

def apply_cap_dynamic(scores_dict, liked_books, top_n=20):
    """스펙 5.2의 cap_dynamic: 유저가 좋아한 L1 분포에 비례해 추천 할당."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    if not good_ids:
        return sorted(scores_dict.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # 유저의 L1 분포
    user_l1_dist = Counter(book_cluster[bid] for bid in good_ids if bid in book_cluster)
    total = sum(user_l1_dist.values())
    quotas = {l1: max(1, round(cnt / total * top_n)) for l1, cnt in user_l1_dist.items()}

    # 나머지 할당
    assigned = sum(quotas.values())
    if assigned < top_n:
        quotas[None] = top_n - assigned  # 기타 장르

    # 점수 순으로 정렬
    sorted_all = sorted(scores_dict.items(), key=lambda x: x[1], reverse=True)

    result = []
    cluster_counts = Counter()
    for bid, score in sorted_all:
        if len(result) >= top_n:
            break
        cl = book_cluster.get(bid)
        quota = quotas.get(cl, quotas.get(None, 0))
        if cluster_counts[cl] < quota:
            result.append((bid, score))
            cluster_counts[cl] += 1
        elif cl not in quotas and cluster_counts[cl] < quotas.get(None, 0):
            result.append((bid, score))
            cluster_counts[cl] += 1

    # 부족하면 나머지로 채움
    if len(result) < top_n:
        used = set(bid for bid, _ in result)
        for bid, score in sorted_all:
            if bid not in used:
                result.append((bid, score))
                if len(result) >= top_n:
                    break

    return result[:top_n]


# cap_dynamic의 다양성 효과 측정
print("\n  cap_dynamic 적용 전후 장르 다양성:")
for label, liked, fb in sim_users:
    cands = stage1_hybrid(liked, fb, 500)
    s2 = batch_score(index, liked, fb, cands, **H10, use_f16_reasons=True)

    # 없이
    top20_raw = sorted(s2.items(), key=lambda x: x[1], reverse=True)[:20]
    raw_clusters = set(book_cluster.get(bid) for bid, _ in top20_raw)

    # 있이
    top20_cap = apply_cap_dynamic(s2, liked, top_n=20)
    cap_clusters = set(book_cluster.get(bid) for bid, _ in top20_cap)

    # 순위 차이
    raw_set = set(bid for bid, _ in top20_raw)
    cap_set = set(bid for bid, _ in top20_cap)
    overlap = len(raw_set & cap_set)

    print(f"  [{label}] raw장르={len(raw_clusters)} cap장르={len(cap_clusters)} "
          f"공통={overlap}/20")


# ===================================================================
# 6. Supabase egress 추정
# ===================================================================
print("\n" + "=" * 70)
print("6. Supabase egress 추정")
print("=" * 70)

# build_index가 fetch하는 데이터량 계산
# books meta: ~200 bytes/row × 9,800
# genre_embeddings: ~16,000 bytes/row (2000 float × 8 bytes JSON) × ~50
# book_v3_vectors: ~16,000 bytes/row × 2,811
# book_love_reasons: ~16,000 bytes/row × 39,446
# (JSON으로 전송되므로 실제로는 숫자가 문자열이라 더 큼)

est_per_embedding_json = DIM * 12  # 각 float가 JSON에서 ~12바이트 ("-0.12345678,")
books_meta_bytes = 9800 * 200  # ~2MB
genre_bytes = 50 * est_per_embedding_json  # ~1.2MB
v3_bytes = 2811 * est_per_embedding_json  # ~67MB
reasons_bytes = 39446 * est_per_embedding_json  # ~947MB

total_per_build = (books_meta_bytes + genre_bytes + v3_bytes + reasons_bytes) / 1024 / 1024
daily_builds = 1  # daily-pipeline

print(f"  build_index 1회 fetch량:")
print(f"    books meta: {books_meta_bytes/1024/1024:.1f}MB")
print(f"    genre embeddings: {genre_bytes/1024/1024:.1f}MB")
print(f"    v3 vectors: {v3_bytes/1024/1024:.1f}MB")
print(f"    reason embeddings: {reasons_bytes/1024/1024:.1f}MB")
print(f"    합계: {total_per_build:.0f}MB")
print(f"  월간 (매일 1회): {total_per_build * 30 / 1024:.1f}GB")
print(f"  Supabase 무료 한도: 5.5GB/월")
print(f"  현재 사용: 12.48GB")
print(f"\n  증분 업데이트 시 (일 ~100권 변경 추정):")
incr_reasons = 100 * 13 * est_per_embedding_json  # 100권 × 13 reasons × embedding
incr_v3 = 100 * est_per_embedding_json
incr_total = (incr_reasons + incr_v3) / 1024 / 1024
print(f"    일 변경분: {incr_total:.1f}MB")
print(f"    월간: {incr_total * 30 / 1024:.2f}GB")
print(f"    절감: {(1 - incr_total * 30 / (total_per_build * 30)) * 100:.0f}%")


# ===================================================================
# 7. Pickle 로드 + startup 시간
# ===================================================================
print("\n" + "=" * 70)
print("7. Pickle 크기 및 startup 시간")
print("=" * 70)

# 현재 index.pkl
current_size = os.path.getsize(pkl_path) / 1024 / 1024
print(f"  현재 index.pkl: {current_size:.1f}MB")

# prestacked 추가 시
bundle_with_prestack = {
    "index": index,
    "meta": books_meta,
    "built_at": built_at,
    "version": "v3-float16",
    "prestacked_f16": prestacked_f16,
    "agg_reason_f16": agg_matrix_f16,
    "desc_matrix_f16": desc_matrix_f16,
}

with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
    tmp_path = f.name
    t0 = time.perf_counter()
    pickle.dump(bundle_with_prestack, f)
    t_write = time.perf_counter() - t0

new_size = os.path.getsize(tmp_path) / 1024 / 1024
print(f"  새 index.pkl (prestacked f16 포함): {new_size:.1f}MB")
print(f"  크기 변화: {current_size:.1f}MB → {new_size:.1f}MB (+{new_size - current_size:.1f}MB)")
print(f"  pickle write: {t_write:.2f}s")

# 로드 시간
t0 = time.perf_counter()
with open(tmp_path, "rb") as f:
    _ = pickle.load(f)
t_load = time.perf_counter() - t0
print(f"  pickle load: {t_load:.2f}s")

# 스케일링 예측
for target in [5000, 10000, 50000]:
    scale = target / N
    est_size = new_size * scale
    est_load = t_load * scale
    print(f"  {target:>6,}권 추정: pkl={est_size:.0f}MB  load={est_load:.1f}s")

os.unlink(tmp_path)

# ===================================================================
# 메모리 총 정리
# ===================================================================
print("\n" + "=" * 70)
print("메모리 사용량 총 정리")
print("=" * 70)

desc_f16_mem = desc_matrix_f16.nbytes / 1024 / 1024
agg_f16_mem = agg_matrix_f16.nbytes / 1024 / 1024
ps_f16_mem = sum(a.nbytes for a in prestacked_f16.values()) / 1024 / 1024
index_overhead = sum(
    bv.desc.nbytes + bv.l1.nbytes + bv.l2.nbytes + sum(r.nbytes for r in bv.reasons)
    for bv in [index.get_book(bid) for bid in all_bids]
) / 1024 / 1024

print(f"  현재 VectorIndex: {index_overhead:.1f}MB")
print(f"  + desc_matrix (f16): {desc_f16_mem:.1f}MB")
print(f"  + agg_reason (f16): {agg_f16_mem:.1f}MB")
print(f"  + prestacked reasons (f16): {ps_f16_mem:.1f}MB")
print(f"  합계 (all f16): {desc_f16_mem + agg_f16_mem + ps_f16_mem:.1f}MB")
print(f"  합계 (기존 + 추가): {index_overhead + desc_f16_mem + agg_f16_mem + ps_f16_mem:.1f}MB")

for target in [5000, 10000, 30000, 50000]:
    scale = target / N
    est_total = (index_overhead + desc_f16_mem + agg_f16_mem + ps_f16_mem) * scale
    render_ok = "Free ✓" if est_total < 400 else ("Starter" if est_total < 1000 else "Pro+")
    print(f"  {target:>6,}권: ~{est_total:.0f}MB ({render_ok})")

print("\n" + "=" * 70)
print("벤치마크 완료")
print("=" * 70)
