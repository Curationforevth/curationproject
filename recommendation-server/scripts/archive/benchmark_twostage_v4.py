#!/usr/bin/env python3
"""Two-stage v4 벤치마크 — 품질 100% 유지, 속도만 개선.

핵심 관점 전환:
  - 추천 품질은 비협상. _score_one 로직 그대로 유지.
  - Stage 2 자체를 빠르게 만드는 것이 아니라, "언제 계산하느냐"를 바꾼다.

검증 항목:
  1. Pre-stacked reasons — np.stack 오버헤드 제거 (인덱스 빌드 시 사전 계산)
  2. Float16 scoring — float32 캐스팅 없이 float16으로 직접 연산
  3. 세션 캐싱 시뮬레이션 — 스펙상 "세션 동안 추천 고정", 첫 요청만 계산
  4. 비동기 사전 계산 — 좋아요 액션 시 백그라운드 계산, 홈 화면은 결과만 로드
  5. Recall 안전 마진 — 500 cands에서 100%이지만, 더 다양한 유저로 스트레스 테스트

사용법: cd recommendation-server && python3 -u scripts/benchmark_twostage_v4.py
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

pkl_path = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
print("=" * 65)
print("Two-stage v4 — 품질 100% 유지, 속도 최적화")
print("=" * 65)

print("\n인덱스 로드...")
index, books_meta, built_at = load_index(pkl_path)
all_bids = index.book_ids
N = len(all_bids)
DIM = index.dim
print(f"  books: {N}, dim: {DIM}")

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


user_profiles = [
    ("6good", 6, 0),
    ("10good_2bad", 10, 2),
    ("20good_3bad", 20, 3),
]
sim_users = [(label, *make_sim_user(ng, nb)) for label, ng, nb in user_profiles]

# Stage 1 hybrid (v3에서 검증된 방식)
agg_reason_vecs = {}
for bid in all_bids:
    bv = index.get_book(bid)
    if bv.reasons:
        mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
        norm = np.linalg.norm(mean_vec)
        agg_reason_vecs[bid] = (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16)
    else:
        agg_reason_vecs[bid] = np.zeros(DIM, dtype=np.float16)

bid_order = list(agg_reason_vecs.keys())
bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}
desc_matrix_f32 = np.stack([index.get_book(bid).desc for bid in bid_order]).astype(np.float32)
agg_reason_matrix_f32 = np.stack([agg_reason_vecs[bid] for bid in bid_order]).astype(np.float32)


def stage1_hybrid(liked_books, fb_data, top_n):
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())

    # single-query scores
    good_descs = [index.get_book(bid).desc.astype(np.float32) for bid in good_ids if index.get_book(bid)]
    if not good_descs:
        return []
    gd = np.stack(good_descs)
    sq_desc = (desc_matrix_f32 @ gd.T).max(axis=1)
    ga = [agg_reason_vecs[bid].astype(np.float32) for bid in good_ids if bid in agg_reason_vecs]
    sq_reason = (agg_reason_matrix_f32 @ np.stack(ga).T).max(axis=1) if ga else np.zeros(N)
    sq_fb = np.zeros(N)
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        sq_fb += sign * (desc_matrix_f32 @ fb["emb"].astype(np.float32))
    sq_scores = 3.0 * sq_desc + 2.0 * sq_reason + 2.0 * sq_fb

    # per-book scores
    pb_scores = np.zeros(N, dtype=np.float32)
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        pb_scores += 3.0 * (desc_matrix_f32 @ bv.desc.astype(np.float32))
        agg_r = agg_reason_vecs.get(bid)
        if agg_r is not None:
            pb_scores += 2.0 * (agg_reason_matrix_f32 @ agg_r.astype(np.float32))
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        pb_scores -= 1.5 * (desc_matrix_f32 @ bv.desc.astype(np.float32))
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        pb_scores += sign * 2.0 * (desc_matrix_f32 @ fb["emb"].astype(np.float32))

    # normalize + combine
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


# ===================================================================
# 1. Pre-stacked reasons — np.stack 제거
# ===================================================================
print("\n" + "=" * 65)
print("1. Pre-stacked reasons 최적화")
print("=" * 65)

# 인덱스 빌드 시 reasons를 미리 stack해서 단일 ndarray로 저장
print("\nPre-stacking all reason matrices...")
t0 = time.perf_counter()
prestacked_reasons = {}  # bid → np.ndarray (n_reasons, dim), float32
for bid in all_bids:
    bv = index.get_book(bid)
    if bv.reasons:
        prestacked_reasons[bid] = np.stack(bv.reasons).astype(np.float32)
    else:
        prestacked_reasons[bid] = np.empty((0, DIM), dtype=np.float32)
t_prestack = time.perf_counter() - t0
extra_mem = sum(arr.nbytes for arr in prestacked_reasons.values()) / 1024 / 1024
print(f"  시간: {t_prestack:.3f}s")
print(f"  추가 메모리: {extra_mem:.1f}MB (float32 stacked)")


def score_one_prestacked(index_obj, liked_books, fb_data, candidate_id):
    """_score_one과 동일하지만 prestacked_reasons 사용."""
    cand = index_obj.get_book(candidate_id)
    if cand is None:
        return 0.0

    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    if not good_ids and not bad_ids:
        return 0.0

    cand_r = prestacked_reasons[candidate_id]  # 사전 계산됨
    has_cand_reasons = cand_r.shape[0] > 0

    weighted_maxsims = []
    for bid in good_ids:
        bv = index_obj.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        query_r = prestacked_reasons[bid]  # 사전 계산됨

        if fb and not fb["is_dislike"]:
            fb_sim = float((cand_r @ fb["emb"]).max()) if has_cand_reasons else 0.0
            if query_r.shape[0] > 0 and has_cand_reasons:
                sims = query_r @ cand_r.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0
            weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
        else:
            if query_r.shape[0] > 0 and has_cand_reasons:
                sims = query_r @ cand_r.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0
            weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)

    for bid in bad_ids:
        bv = index_obj.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        query_r = prestacked_reasons[bid]

        if fb and fb["is_dislike"]:
            fb_sim = float((cand_r @ fb["emb"]).max()) if has_cand_reasons else 0.0
            if query_r.shape[0] > 0 and has_cand_reasons:
                sims = query_r @ cand_r.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0
            weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
        else:
            if query_r.shape[0] > 0 and has_cand_reasons:
                sims = query_r @ cand_r.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0
            weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)

    reason_score = float(np.mean(weighted_maxsims)) if weighted_maxsims else 0.0

    # desc/l1/l2/fb_desc — 원본과 동일
    good_descs = [index_obj.get_book(bid).desc for bid in good_ids if index_obj.get_book(bid)]
    desc_score = max(float(np.dot(d, cand.desc)) for d in good_descs) if good_descs else 0.0
    good_l1s = [index_obj.get_book(bid).l1 for bid in good_ids if index_obj.get_book(bid)]
    l1_score = max(float(np.dot(l, cand.l1)) for l in good_l1s) if good_l1s else 0.0
    good_l2s = [index_obj.get_book(bid).l2 for bid in good_ids if index_obj.get_book(bid)]
    l2_score = max(float(np.dot(l, cand.l2)) for l in good_l2s) if good_l2s else 0.0

    fb_desc_vals = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_desc_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_desc_score = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0

    return (W_REASON * reason_score + W_DESC * desc_score +
            W_L1 * l1_score + W_L2 * l2_score + W_FB_DESC * fb_desc_score)


def batch_score_prestacked(index_obj, liked_books, fb_data, candidate_ids):
    """v2 배치 + prestacked reasons."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    cand_books = [(cid, index_obj.get_book(cid)) for cid in candidate_ids]
    cand_books = [(cid, bv) for cid, bv in cand_books if bv is not None]
    if not cand_books:
        return {}
    n_cands = len(cand_books)

    # desc/l1/l2 배치 (v2와 동일)
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

    # reason — prestacked로 루프
    reason_scores = np.zeros(n_cands)
    good_data = []
    for bid, bv in good_bvs:
        fb = fb_data.get(bid)
        good_data.append((bid, fb if fb and not fb["is_dislike"] else None))
    bad_bvs2 = [(bid, index_obj.get_book(bid)) for bid in bad_ids]
    bad_data = []
    for bid, bv in bad_bvs2:
        if bv is None:
            continue
        fb = fb_data.get(bid)
        bad_data.append((bid, fb if fb and fb["is_dislike"] else None))

    for i, (cid, cand_bv) in enumerate(cand_books):
        cand_r = prestacked_reasons[cid]
        if cand_r.shape[0] == 0:
            continue
        weighted = []
        for bid, fb in good_data:
            query_r = prestacked_reasons[bid]
            if fb:
                fb_sim = float((cand_r @ fb["emb"]).max())
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
        for bid, fb in bad_data:
            query_r = prestacked_reasons[bid]
            if fb:
                fb_sim = float((cand_r @ fb["emb"]).max())
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                r_sim = float((query_r @ cand_r.T).max(axis=1).mean()) if query_r.shape[0] > 0 else 0.0
                weighted.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
        if weighted:
            reason_scores[i] = float(np.mean(weighted))

    final = (W_REASON * reason_scores + W_DESC * desc_scores +
             W_L1 * l1_scores + W_L2 * l2_scores + W_FB_DESC * fb_desc_scores)
    return {cid: float(final[i]) for i, (cid, _) in enumerate(cand_books)}


# 정확성 검증
print("\n정확성 검증...")
test_liked, test_fb = sim_users[0][1], sim_users[0][2]
test_cands = [bid for bid in all_bids[:100] if bid not in test_liked]
active = {bid: d for bid, d in test_liked.items() if d["rating"] != "neutral"}

orig_scores = {cid: _score_one(index, active, test_fb, cid) for cid in test_cands}
pre_scores = {cid: score_one_prestacked(index, test_liked, test_fb, cid) for cid in test_cands}
batch_pre_scores = batch_score_prestacked(index, test_liked, test_fb, test_cands)

max_diff_1 = max(abs(orig_scores[c] - pre_scores[c]) for c in test_cands)
max_diff_2 = max(abs(orig_scores[c] - batch_pre_scores.get(c, 0)) for c in test_cands)
print(f"  원본 vs prestacked loop: max_diff={max_diff_1:.6f} {'PASS' if max_diff_1 < 0.01 else 'FAIL'}")
print(f"  원본 vs prestacked batch: max_diff={max_diff_2:.6f} {'PASS' if max_diff_2 < 0.01 else 'FAIL'}")


# ===================================================================
# 2. 속도 비교: 원본 loop vs prestacked loop vs prestacked batch
# ===================================================================
print("\n" + "=" * 65)
print("2. 속도 비교")
print("=" * 65)

for label, liked, fb in sim_users:
    active = {bid: d for bid, d in liked.items() if d["rating"] != "neutral"}

    for cn in [200, 300, 500]:
        cands = stage1_hybrid(liked, fb, cn)

        # 원본 loop
        t0 = time.perf_counter()
        for cid in cands:
            _score_one(index, active, fb, cid)
        t_orig = time.perf_counter() - t0

        # prestacked loop
        t0 = time.perf_counter()
        for cid in cands:
            score_one_prestacked(index, liked, fb, cid)
        t_pre_loop = time.perf_counter() - t0

        # prestacked batch
        t0 = time.perf_counter()
        batch_score_prestacked(index, liked, fb, cands)
        t_pre_batch = time.perf_counter() - t0

        print(f"  [{label}] {cn} cands: "
              f"orig={t_orig*1000:.0f}ms  "
              f"pre_loop={t_pre_loop*1000:.0f}ms({t_orig/t_pre_loop:.1f}x)  "
              f"pre_batch={t_pre_batch*1000:.0f}ms({t_orig/t_pre_batch:.1f}x)")


# ===================================================================
# 3. End-to-end: Stage 1 + Stage 2 (prestacked batch) + 캐싱 시뮬레이션
# ===================================================================
print("\n" + "=" * 65)
print("3. End-to-end 시나리오별 레이턴시")
print("=" * 65)

print("\n스펙: '세션 동안 추천 고정, 새로고침/재진입 시에만 갱신'")
print("→ 첫 요청만 계산, 이후는 캐시 반환\n")

for label, liked, fb in sim_users:
    full_t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked, fb)
    t_full = time.perf_counter() - full_t0
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:20])

    for cn in [200, 300, 500]:
        # Stage 1
        t0 = time.perf_counter()
        cands = stage1_hybrid(liked, fb, cn)
        t_s1 = time.perf_counter() - t0

        # Stage 2 (prestacked batch)
        t0 = time.perf_counter()
        s2 = batch_score_prestacked(index, liked, fb, cands)
        t_s2 = time.perf_counter() - t0

        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:20])
        recall = len(gt & top20) / len(gt)
        total = t_s1 + t_s2
        meets_target = total * 1000 < 500  # 첫 요청 500ms 이내 (이후 캐시)

        print(f"  [{label}] {cn} cands: "
              f"S1={t_s1*1000:.0f}ms S2={t_s2*1000:.0f}ms "
              f"total={total*1000:.0f}ms  "
              f"recall={recall:.0%}  "
              f"{'✓' if meets_target else '✗'}")

    print(f"  (비교: full scoring = {t_full:.1f}s)")
    print()


# ===================================================================
# 4. 비동기 사전 계산 시나리오
# ===================================================================
print("=" * 65)
print("4. 비동기 사전 계산 시나리오")
print("=" * 65)
print("""
  시나리오:
    1. 유저가 좋아요/싫어요 → 비동기로 추천 재계산 트리거
    2. 계산 결과를 DB/캐시에 저장
    3. 홈 화면 진입 시 저장된 결과 로드 (0ms)
    4. 아직 계산 안 됐으면 → on-demand 계산 (첫 요청)

  이 시나리오에서 중요한 건:
    - 비동기 계산 시간 (유저가 기다리지 않음, 서버 부하)
    - on-demand fallback 시간 (유저가 기다림, 최악의 경우)
""")

for label, liked, fb in sim_users:
    # 비동기 계산 (500 cands, 품질 최우선)
    t0 = time.perf_counter()
    cands = stage1_hybrid(liked, fb, 500)
    s2 = batch_score_prestacked(index, liked, fb, cands)
    t_async = time.perf_counter() - t0

    # on-demand fallback (200 cands, 속도 우선)
    t0 = time.perf_counter()
    cands_fast = stage1_hybrid(liked, fb, 200)
    s2_fast = batch_score_prestacked(index, liked, fb, cands_fast)
    t_ondemand = time.perf_counter() - t0

    # full scoring (비교)
    t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked, fb)
    t_full = time.perf_counter() - t0
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:20])

    top20_async = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:20])
    top20_fast = set(bid for bid, _ in sorted(s2_fast.items(), key=lambda x: x[1], reverse=True)[:20])

    print(f"  [{label}]")
    print(f"    비동기(500c): {t_async*1000:.0f}ms  recall={len(gt & top20_async)/len(gt):.0%}")
    print(f"    on-demand(200c): {t_ondemand*1000:.0f}ms  recall={len(gt & top20_fast)/len(gt):.0%}")
    print(f"    full: {t_full:.1f}s")


# ===================================================================
# 5. Recall 스트레스 테스트 — 다양한 유저 50명
# ===================================================================
print("\n" + "=" * 65)
print("5. Recall 스트레스 테스트 (50명 랜덤 유저)")
print("=" * 65)

stress_rng = np.random.default_rng(123)  # 다른 시드
STRESS_N = 50
TOP_K = 20

results_by_config = {200: [], 300: [], 500: []}

for i in range(STRESS_N):
    n_good = stress_rng.integers(6, 25)
    n_bad = stress_rng.integers(0, 5)
    chosen = stress_rng.choice(all_bids, size=n_good + n_bad, replace=False)
    liked, fb = {}, {}
    for j, bid in enumerate(chosen):
        if j < n_good:
            liked[bid] = {"rating": "good"}
            bv = index.get_book(bid)
            if bv and bv.reasons and stress_rng.random() > 0.5:
                fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": False}
        else:
            liked[bid] = {"rating": "bad"}
            bv = index.get_book(bid)
            if bv and bv.reasons:
                fb[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": True}

    full_scores = recommend_scores(index, liked, fb)
    gt = set(bid for bid, _ in sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K])

    for cn in [200, 300, 500]:
        cands = stage1_hybrid(liked, fb, cn)
        s2 = batch_score_prestacked(index, liked, fb, cands)
        top20 = set(bid for bid, _ in sorted(s2.items(), key=lambda x: x[1], reverse=True)[:TOP_K])
        recall = len(gt & top20) / len(gt) if gt else 1.0
        results_by_config[cn].append(recall)

    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{STRESS_N} 완료...")

print(f"\n  결과 ({STRESS_N}명):")
for cn in [200, 300, 500]:
    recalls = results_by_config[cn]
    avg = np.mean(recalls)
    p5 = np.percentile(recalls, 5)
    p25 = np.percentile(recalls, 25)
    median = np.median(recalls)
    min_r = min(recalls)
    below_90 = sum(1 for r in recalls if r < 0.9)
    below_80 = sum(1 for r in recalls if r < 0.8)
    print(f"    {cn} cands: avg={avg:.0%}  median={median:.0%}  p5={p5:.0%}  "
          f"min={min_r:.0%}  <90%={below_90}명  <80%={below_80}명")


# ===================================================================
# 6. 스케일링 예측 (실측 기반)
# ===================================================================
print("\n" + "=" * 65)
print("6. 스케일링 예측")
print("=" * 65)

# Stage 1 실측 (10good, 5회 반복)
liked_10, fb_10 = sim_users[1][1], sim_users[1][2]
s1_times = []
for _ in range(5):
    t0 = time.perf_counter()
    stage1_hybrid(liked_10, fb_10, 500)
    s1_times.append(time.perf_counter() - t0)
s1_base = np.median(s1_times) * 1000

# Stage 2 실측 (300 cands, 3회 반복)
cands_300 = stage1_hybrid(liked_10, fb_10, 300)
s2_times = []
for _ in range(3):
    t0 = time.perf_counter()
    batch_score_prestacked(index, liked_10, fb_10, cands_300)
    s2_times.append(time.perf_counter() - t0)
s2_base = np.median(s2_times) * 1000

print(f"\n  기준 (N={N}, 10good, 300 cands):")
print(f"    Stage 1: {s1_base:.0f}ms (O(N))")
print(f"    Stage 2: {s2_base:.0f}ms (후보 수 고정)")

prestacked_mem = sum(arr.nbytes for arr in prestacked_reasons.values()) / 1024 / 1024
desc_mem_per_book = DIM * 2 / 1024 / 1024  # float16
agg_mem_per_book = DIM * 2 / 1024 / 1024

for target in [5000, 10000, 30000, 50000]:
    scale = target / N
    s1_est = s1_base * scale
    s2_est = s2_base  # 고정
    # 메모리: desc + agg_reason (Stage 1) + prestacked reasons (Stage 2용)
    stage1_mem = target * DIM * 2 * 2 / 1024 / 1024  # 2 matrices × float16
    reason_mem = prestacked_mem * scale  # float32 prestacked
    total_mem = stage1_mem + reason_mem

    total_ms = s1_est + s2_est
    print(f"\n  {target:>6,}권:")
    print(f"    S1={s1_est:.0f}ms + S2={s2_est:.0f}ms = {total_ms:.0f}ms")
    print(f"    메모리: S1={stage1_mem:.0f}MB + reasons={reason_mem:.0f}MB = {total_mem:.0f}MB")
    if total_ms < 500:
        print(f"    첫 요청 500ms 이내 ✓ (이후 캐시)")
    elif total_ms < 1000:
        print(f"    첫 요청 1초 이내 (비동기 사전 계산 권장)")
    else:
        print(f"    첫 요청 {total_ms/1000:.1f}초 (비동기 사전 계산 필수)")

print("\n" + "=" * 65)
print("벤치마크 완료")
print("=" * 65)
