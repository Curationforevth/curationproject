"""Two-stage 추천 엔진. Stage 1 (후보 선별) + Stage 2 (정밀 스코어링)."""
from __future__ import annotations

import numpy as np


def stage1_hybrid(
    liked_books: dict,
    fb_data: dict,
    desc_matrix_f16: np.ndarray,
    agg_reason_matrix_f16: np.ndarray,
    bid_order: list[str],
    top_n: int = 700,
) -> list[str]:
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())

    if not good_ids:
        return []

    N = len(bid_order)
    bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}

    dm = desc_matrix_f16.astype(np.float32)
    am = agg_reason_matrix_f16.astype(np.float32)

    good_desc_indices = [bid_to_idx[bid] for bid in good_ids if bid in bid_to_idx]
    if not good_desc_indices:
        return []
    good_descs = dm[good_desc_indices]
    good_aggs = am[good_desc_indices]

    # single-query scores
    sq_desc = (dm @ good_descs.T).max(axis=1)
    sq_reason = (am @ good_aggs.T).max(axis=1)
    sq_fb = np.zeros(N, dtype=np.float32)
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        sq_fb += sign * (dm @ fb["emb"].astype(np.float32))
    sq_scores = 3.0 * sq_desc + 2.0 * sq_reason + 2.0 * sq_fb

    # per-book scores
    pb_scores = np.zeros(N, dtype=np.float32)
    for bid in good_ids:
        idx = bid_to_idx.get(bid)
        if idx is None:
            continue
        pb_scores += 3.0 * (dm @ dm[idx])
        pb_scores += 2.0 * (am @ am[idx])
    for bid in bad_ids:
        idx = bid_to_idx.get(bid)
        if idx is None:
            continue
        pb_scores -= 1.5 * (dm @ dm[idx])
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        pb_scores += sign * 2.0 * (dm @ fb["emb"].astype(np.float32))

    # normalize + combine
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

    combined = sq_norm + pb_norm
    for rid in read_ids:
        idx = bid_to_idx.get(rid)
        if idx is not None:
            combined[idx] = -999.0

    top_idx = np.argsort(combined)[::-1][:top_n]
    return [bid_order[i] for i in top_idx if combined[i] > -900.0]
