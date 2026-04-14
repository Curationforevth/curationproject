#!/usr/bin/env python3
"""Two-stage 추천 아키텍처 벤치마크.

검증 항목:
  1. Reason 평균벡터 recall — aggregated reason으로 후보 필터링 시 recall 손실 측정
  2. Two-stage 레이턴시 — 현재 방식 vs two-stage 속도 비교
  3. 메모리 사용량 — 현재 vs two-stage 구조의 메모리 비교

사용법: cd recommendation-server && python scripts/benchmark_twostage.py
필요: data/index.pkl (기존 빌드된 인덱스)
"""
from __future__ import annotations

import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from engine.index import VectorIndex, BookVectors
from engine.loader import load_index
from engine.scorer import recommend_scores, _score_one

# ---------------------------------------------------------------------------
# 1. 인덱스 로드
# ---------------------------------------------------------------------------
print("=" * 60)
print("Two-stage 추천 아키텍처 벤치마크")
print("=" * 60)

pkl_path = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
if not os.path.exists(pkl_path):
    print(f"ERROR: index.pkl not found at {pkl_path}")
    sys.exit(1)

print("\n[1/5] 인덱스 로드...")
index, books_meta, built_at = load_index(pkl_path)
all_bids = index.book_ids
N = len(all_bids)
print(f"  books: {N}")
print(f"  built_at: {built_at}")

# 각 책의 reason 개수 통계
reason_counts = [len(index.get_book(bid).reasons) for bid in all_bids]
total_reasons = sum(reason_counts)
avg_reasons = total_reasons / N if N > 0 else 0
print(f"  total reasons: {total_reasons}")
print(f"  avg reasons/book: {avg_reasons:.1f}")

# ---------------------------------------------------------------------------
# 2. Aggregated reason matrix 구축
# ---------------------------------------------------------------------------
print("\n[2/5] Aggregated reason matrix 구축...")
t0 = time.perf_counter()

agg_reason_vecs = {}
for bid in all_bids:
    bv = index.get_book(bid)
    if bv.reasons:
        mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
        norm = np.linalg.norm(mean_vec)
        agg_reason_vecs[bid] = (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16)
    else:
        agg_reason_vecs[bid] = np.zeros(index.dim, dtype=np.float16)

# 행렬 구축 (desc + agg_reason)
agg_bid_order = list(agg_reason_vecs.keys())
agg_reason_matrix = np.stack([agg_reason_vecs[bid] for bid in agg_bid_order])
desc_matrix = np.stack([index.get_book(bid).desc for bid in agg_bid_order])

t1 = time.perf_counter()
print(f"  구축 시간: {t1 - t0:.3f}s")
print(f"  desc_matrix shape: {desc_matrix.shape}")
print(f"  agg_reason_matrix shape: {agg_reason_matrix.shape}")
desc_mb = desc_matrix.nbytes / 1024 / 1024
agg_mb = agg_reason_matrix.nbytes / 1024 / 1024
print(f"  desc_matrix 메모리: {desc_mb:.1f} MB")
print(f"  agg_reason_matrix 메모리: {agg_mb:.1f} MB")
print(f"  합계 (two-stage Stage 1): {desc_mb + agg_mb:.1f} MB")

# 전체 reason 벡터 메모리 계산
total_reason_bytes = sum(
    sum(r.nbytes for r in index.get_book(bid).reasons) for bid in all_bids
)
print(f"  전체 reason 벡터 메모리: {total_reason_bytes / 1024 / 1024:.1f} MB")

# ---------------------------------------------------------------------------
# 3. 시뮬레이션 유저 생성
# ---------------------------------------------------------------------------
print("\n[3/5] 시뮬레이션 유저 생성...")

rng = np.random.default_rng(42)

def make_sim_user(n_good: int, n_bad: int = 0) -> tuple[dict, dict]:
    """랜덤으로 좋아요/싫어요 책을 골라 시뮬레이션 유저 생성."""
    chosen = rng.choice(all_bids, size=n_good + n_bad, replace=False)
    liked_books = {}
    fb_data = {}
    for i, bid in enumerate(chosen):
        if i < n_good:
            liked_books[bid] = {"rating": "good"}
            # 일부 책에 대해 feedback embedding 시뮬레이션
            bv = index.get_book(bid)
            if bv and bv.reasons and rng.random() > 0.5:
                fb_data[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": False}
        else:
            liked_books[bid] = {"rating": "bad"}
            bv = index.get_book(bid)
            if bv and bv.reasons:
                fb_data[bid] = {"emb": bv.reasons[0].astype(np.float32), "is_dislike": True}
    return liked_books, fb_data


# 다양한 유저 프로파일 생성
user_profiles = [
    ("Tier2_small", 6, 0),
    ("Tier2_medium", 10, 2),
    ("Tier2_large", 20, 3),
]

sim_users = []
for label, n_good, n_bad in user_profiles:
    liked, fb = make_sim_user(n_good, n_bad)
    sim_users.append((label, liked, fb))
    print(f"  {label}: {n_good} good, {n_bad} bad, {len(fb)} feedback")

# ---------------------------------------------------------------------------
# 4. Recall 테스트 — Stage 1 후보에서 full scoring top-K를 얼마나 포함하는가
# ---------------------------------------------------------------------------
print("\n[4/5] Recall 테스트...")
print("-" * 60)

TOP_K_FINAL = 20  # 최종 추천 개수
CANDIDATE_SIZES = [50, 100, 200, 300, 500]  # Stage 1 후보 수

# desc 가중치와 reason 가중치 비율 (Stage 1 필터링용)
STAGE1_DESC_WEIGHT = 3.0
STAGE1_REASON_WEIGHT = 2.0


def stage1_candidates(liked_books: dict, fb_data: dict, top_n: int) -> list[str]:
    """Stage 1: desc + agg_reason 행렬곱으로 후보 선별."""
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    read_ids = set(liked_books.keys())

    # desc 유사도: 각 good book의 desc와 전체 책의 cosine
    good_descs = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is not None:
            good_descs.append(bv.desc.astype(np.float32))

    if not good_descs:
        return []

    # desc: max cosine to any good book (벡터화)
    good_desc_mat = np.stack(good_descs)  # (n_good, dim)
    desc_scores = (desc_matrix.astype(np.float32) @ good_desc_mat.T).max(axis=1)  # (N,)

    # agg_reason: 각 good book의 agg_reason과의 max cosine
    good_agg_reasons = []
    for bid in good_ids:
        if bid in agg_reason_vecs:
            good_agg_reasons.append(agg_reason_vecs[bid].astype(np.float32))

    if good_agg_reasons:
        good_agg_mat = np.stack(good_agg_reasons)  # (n_good, dim)
        reason_scores = (agg_reason_matrix.astype(np.float32) @ good_agg_mat.T).max(axis=1)
    else:
        reason_scores = np.zeros(len(agg_bid_order))

    # 가중합
    combined = STAGE1_DESC_WEIGHT * desc_scores + STAGE1_REASON_WEIGHT * reason_scores

    # read_ids 제외
    bid_to_idx = {bid: i for i, bid in enumerate(agg_bid_order)}
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            combined[idx] = -999.0

    top_idx = np.argsort(combined)[::-1][:top_n]
    return [agg_bid_order[i] for i in top_idx]


for label, liked_books, fb_data in sim_users:
    print(f"\n  [{label}]")

    # Full scoring (ground truth)
    t0 = time.perf_counter()
    full_scores = recommend_scores(index, liked_books, fb_data)
    t_full = time.perf_counter() - t0

    top_k_full = sorted(full_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K_FINAL]
    ground_truth = set(bid for bid, _ in top_k_full)

    print(f"  Full scoring: {t_full:.3f}s ({len(full_scores)} candidates)")

    for cand_size in CANDIDATE_SIZES:
        t0 = time.perf_counter()
        candidates = stage1_candidates(liked_books, fb_data, cand_size)
        t_stage1 = time.perf_counter() - t0

        # Stage 2: full scoring on candidates only
        t0 = time.perf_counter()
        stage2_scores = {}
        active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
        for cid in candidates:
            stage2_scores[cid] = _score_one(index, active, fb_data, cid)
        t_stage2 = time.perf_counter() - t0

        top_k_twostage = sorted(stage2_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K_FINAL]
        retrieved = set(bid for bid, _ in top_k_twostage)

        recall = len(ground_truth & retrieved) / len(ground_truth) if ground_truth else 0
        total_time = t_stage1 + t_stage2
        speedup = t_full / total_time if total_time > 0 else 0

        print(f"    top-{cand_size:>4}: recall={recall:.0%}  "
              f"stage1={t_stage1*1000:.1f}ms  stage2={t_stage2*1000:.1f}ms  "
              f"total={total_time*1000:.1f}ms  speedup={speedup:.1f}x")

# ---------------------------------------------------------------------------
# 5. 스케일링 시뮬레이션 — 5만권에서 예상 수치
# ---------------------------------------------------------------------------
print("\n[5/5] 스케일링 예측...")
print("-" * 60)

SCALE_TARGETS = [10_000, 30_000, 50_000, 100_000]
avg_reasons_per_book = avg_reasons

for target in SCALE_TARGETS:
    scale = target / N if N > 0 else 1

    # 메모리 예측
    desc_mem = target * index.dim * 2 / 1024 / 1024  # float16
    agg_reason_mem = desc_mem  # same size
    full_reason_mem = target * avg_reasons_per_book * index.dim * 2 / 1024 / 1024
    twostage_mem = desc_mem + agg_reason_mem
    current_mem = desc_mem + full_reason_mem + desc_mem * 2  # desc + reasons + l1 + l2

    # 레이턴시 예측 (현재 벤치마크에서 선형 외삽)
    # Tier2_large의 full scoring 시간을 기준으로
    ref_full_time = None
    for label, liked_books, fb_data in sim_users:
        if label == "Tier2_large":
            t0 = time.perf_counter()
            _ = recommend_scores(index, liked_books, fb_data)
            ref_full_time = time.perf_counter() - t0
            break

    full_latency_ms = (ref_full_time * scale * 1000) if ref_full_time else 0
    # two-stage는 거의 상수 (stage1 행렬곱 + stage2 고정 500권)
    # stage1은 O(N) 행렬곱이지만 매우 빠름
    stage1_est_ms = 0.5 * scale  # numpy 행렬곱은 ~0.5ms at current scale
    stage2_est_ms = 50  # 고정 500 후보

    render_ok = "OK" if twostage_mem < 400 else ("주의" if twostage_mem < 500 else "OOM")

    print(f"\n  {target:,}권:")
    print(f"    현재 방식 메모리: {current_mem:.0f} MB | 레이턴시: {full_latency_ms:.0f}ms")
    print(f"    Two-stage 메모리: {twostage_mem:.0f} MB | 레이턴시: {stage1_est_ms + stage2_est_ms:.0f}ms")
    print(f"    메모리 절감: {(1 - twostage_mem / current_mem) * 100:.0f}%")
    print(f"    Render free tier: {render_ok}")

print("\n" + "=" * 60)
print("벤치마크 완료")
print("=" * 60)
