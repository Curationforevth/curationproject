"""Two-stage 추천 엔진. Stage 1 (후보 선별) + Stage 2 (정밀 스코어링)."""
from __future__ import annotations

import numpy as np

from engine.index import VectorIndex
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT)


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


def batch_score_prestacked(
    index: VectorIndex,
    liked_books: dict,
    fb_data: dict,
    candidate_ids: list,
    prestacked_reasons: dict,          # {bid: (n_reasons, dim) float16}
    w_reason: float = W_REASON,
    w_desc: float = W_DESC,
    w_l1: float = W_L1,
    w_l2: float = W_L2,
    w_fb_desc: float = W_FB_DESC,
) -> dict:
    """Stage 2 정밀 스코어링.

    _score_one()과 동일한 알고리즘이지만, prestacked reason 배열을 사용해
    np.stack() 오버헤드를 제거한다.  최대 오차 < 0.01 (float16 양자화 기인).
    """
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return {}

    # 좋아요 책 벡터를 미리 수집
    good_books = {bid: index.get_book(bid) for bid in good_ids}
    good_books = {bid: bv for bid, bv in good_books.items() if bv is not None}

    scores: dict = {}

    for cid in candidate_ids:
        cand = index.get_book(cid)
        if cand is None:
            continue

        # ── 1. reason_score (per-candidate 루프, prestacked 사용) ──────────
        # cand_reasons: (n_cand_reasons, dim) float32
        if cid in prestacked_reasons and prestacked_reasons[cid].shape[0] > 0:
            cand_reasons_f32 = prestacked_reasons[cid].astype(np.float32)
        elif cand.reasons:
            cand_reasons_f32 = np.stack(cand.reasons).astype(np.float32)
        else:
            cand_reasons_f32 = None

        weighted_maxsims = []

        for bid in good_ids:
            bv = good_books.get(bid)
            if bv is None:
                continue
            fb = fb_data.get(bid)

            # query reasons: prestacked 우선, fallback으로 np.stack
            if bid in prestacked_reasons and prestacked_reasons[bid].shape[0] > 0:
                q_reasons_f32 = prestacked_reasons[bid].astype(np.float32)
            elif bv.reasons:
                q_reasons_f32 = np.stack(bv.reasons).astype(np.float32)
            else:
                q_reasons_f32 = None

            if cand_reasons_f32 is not None and q_reasons_f32 is not None:
                sims = q_reasons_f32 @ cand_reasons_f32.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0

            if fb and not fb["is_dislike"]:
                if cand_reasons_f32 is not None:
                    fb_sim = float((cand_reasons_f32 @ fb["emb"].astype(np.float32)).max())
                else:
                    fb_sim = 0.0
                weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
            else:
                weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)

        for bid in bad_ids:
            bv = index.get_book(bid)
            if bv is None:
                continue
            fb = fb_data.get(bid)

            # query reasons: prestacked 우선
            if bid in prestacked_reasons and prestacked_reasons[bid].shape[0] > 0:
                q_reasons_f32 = prestacked_reasons[bid].astype(np.float32)
            elif bv.reasons:
                q_reasons_f32 = np.stack(bv.reasons).astype(np.float32)
            else:
                q_reasons_f32 = None

            if cand_reasons_f32 is not None and q_reasons_f32 is not None:
                sims = q_reasons_f32 @ cand_reasons_f32.T
                r_sim = float(sims.max(axis=1).mean())
            else:
                r_sim = 0.0

            if fb and fb["is_dislike"]:
                if cand_reasons_f32 is not None:
                    fb_sim = float((cand_reasons_f32 @ fb["emb"].astype(np.float32)).max())
                else:
                    fb_sim = 0.0
                weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
            else:
                weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)

        reason_score = float(np.mean(weighted_maxsims)) if weighted_maxsims else 0.0

        # ── 2. desc_score ─────────────────────────────────────────────────
        cand_desc = cand.desc.astype(np.float32)
        good_descs = [good_books[bid].desc.astype(np.float32) for bid in good_ids if bid in good_books]
        desc_score = float(max(float(np.dot(d, cand_desc)) for d in good_descs)) if good_descs else 0.0

        # ── 3. l1_score ───────────────────────────────────────────────────
        good_l1s = [good_books[bid].l1.astype(np.float32) for bid in good_ids if bid in good_books]
        cand_l1 = cand.l1.astype(np.float32)
        l1_score = float(max(float(np.dot(l, cand_l1)) for l in good_l1s)) if good_l1s else 0.0

        # ── 4. l2_score ───────────────────────────────────────────────────
        good_l2s = [good_books[bid].l2.astype(np.float32) for bid in good_ids if bid in good_books]
        cand_l2 = cand.l2.astype(np.float32)
        l2_score = float(max(float(np.dot(l, cand_l2)) for l in good_l2s)) if good_l2s else 0.0

        # ── 5. fb_desc_score ──────────────────────────────────────────────
        fb_desc_vals = []
        for bid, fb in fb_data.items():
            if liked_books.get(bid, {}).get("rating") == "neutral":
                continue
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_desc_vals.append(sign * float(np.dot(fb["emb"].astype(np.float32), cand_desc)))
        fb_desc_score = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0

        scores[cid] = (
            w_reason * reason_score
            + w_desc * desc_score
            + w_l1 * l1_score
            + w_l2 * l2_score
            + w_fb_desc * fb_desc_score
        )

    return scores
