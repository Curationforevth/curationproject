"""검증 기준선(golden reference) — prod 미사용.

Phase 2 벡터화(2026-07-02) 직전의 stage1_hybrid / batch_score_prestacked 를
**그대로 보존**한 사본. tests/test_twostage_equivalence.py 와
scripts/verify_equivalence.py 가 신규(벡터화) 구현과의 점수·순위 동일성을
증명하는 데 쓴다. 스코어링 수식이 의도적으로 바뀌기 전까지 수정 금지.
"""
from __future__ import annotations

import numpy as np

from engine.index import VectorIndex
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT, SOURCE_TIER_PENALTY)

STAGE1_CHUNK = 1024


def stage1_hybrid_reference(
    liked_books: dict,
    fb_data: dict,
    desc_matrix_f16: np.ndarray,
    agg_reason_matrix_f16: np.ndarray,
    bid_order: list[str],
    top_n: int = 700,
    extra_query: dict | None = None,
) -> list[str]:
    extra_query = extra_query or {}

    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]
    read_ids = set(liked_books.keys())

    if not good_ids:
        return []

    N = len(bid_order)
    bid_to_idx = {bid: i for i, bid in enumerate(bid_order)}

    good_desc_indices = [bid_to_idx[bid] for bid in good_ids if bid in bid_to_idx]
    extra_good = [bid for bid in good_ids if bid not in bid_to_idx and bid in extra_query]
    extra_bad = [bid for bid in bad_ids if bid not in bid_to_idx and bid in extra_query]
    if not good_desc_indices and not extra_good:
        return []

    D = desc_matrix_f16.shape[1]

    def _rows_f32(mat, idxs):
        return mat[idxs].astype(np.float32) if idxs else np.zeros((0, D), np.float32)

    idx_descs = _rows_f32(desc_matrix_f16, good_desc_indices)
    idx_aggs = _rows_f32(agg_reason_matrix_f16, good_desc_indices)
    if extra_good:
        ex_descs = np.stack([extra_query[b].desc.astype(np.float32) for b in extra_good])
        ex_aggs = np.zeros((len(extra_good), D), np.float32)
        good_descs = np.vstack([idx_descs, ex_descs])
        good_aggs = np.vstack([idx_aggs, ex_aggs])
    else:
        good_descs = idx_descs
        good_aggs = idx_aggs
    good_descs_T = good_descs.T
    good_aggs_T = good_aggs.T

    fb_terms = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_terms.append((sign, fb["emb"].astype(np.float32)))

    pb_desc_terms = []
    pb_agg_terms = []
    for bid in good_ids:
        idx = bid_to_idx.get(bid)
        if idx is None:
            continue
        pb_desc_terms.append((3.0, desc_matrix_f16[idx].astype(np.float32)))
        pb_agg_terms.append((2.0, agg_reason_matrix_f16[idx].astype(np.float32)))
    for bid in bad_ids:
        idx = bid_to_idx.get(bid)
        if idx is None:
            continue
        pb_desc_terms.append((-1.5, desc_matrix_f16[idx].astype(np.float32)))
    for bid in extra_good:
        pb_desc_terms.append((3.0, extra_query[bid].desc.astype(np.float32)))
    for bid in extra_bad:
        pb_desc_terms.append((-1.5, extra_query[bid].desc.astype(np.float32)))
    for sign, emb in fb_terms:
        pb_desc_terms.append((sign * 2.0, emb))

    sq_scores = np.empty(N, dtype=np.float32)
    pb_scores = np.zeros(N, dtype=np.float32)
    for s in range(0, N, STAGE1_CHUNK):
        e = min(s + STAGE1_CHUNK, N)
        dm_blk = desc_matrix_f16[s:e].astype(np.float32)
        am_blk = agg_reason_matrix_f16[s:e].astype(np.float32)
        b = e - s

        sq_desc_blk = (dm_blk @ good_descs_T).max(axis=1)
        sq_reason_blk = (am_blk @ good_aggs_T).max(axis=1) if good_aggs.shape[0] else np.zeros(b, np.float32)
        sq_fb_blk = np.zeros(b, dtype=np.float32)
        for sign, emb in fb_terms:
            sq_fb_blk += sign * (dm_blk @ emb)
        sq_scores[s:e] = 3.0 * sq_desc_blk + 2.0 * sq_reason_blk + 2.0 * sq_fb_blk

        pb_blk = np.zeros(b, dtype=np.float32)
        for coef, q in pb_desc_terms:
            pb_blk += coef * (dm_blk @ q)
        for coef, q in pb_agg_terms:
            pb_blk += coef * (am_blk @ q)
        pb_scores[s:e] = pb_blk

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


def batch_score_prestacked_reference(
    index: VectorIndex,
    liked_books: dict,
    fb_data: dict,
    candidate_ids: list,
    prestacked_reasons: dict,
    w_reason: float = W_REASON,
    w_desc: float = W_DESC,
    w_l1: float = W_L1,
    w_l2: float = W_L2,
    w_fb_desc: float = W_FB_DESC,
    extra_query: dict | None = None,
) -> dict:
    extra_query = extra_query or {}
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return {}

    good_books = {bid: (index.get_book(bid) or extra_query.get(bid)) for bid in good_ids}
    good_books = {bid: bv for bid, bv in good_books.items() if bv is not None}

    scores: dict = {}

    for cid in candidate_ids:
        cand = index.get_book(cid)
        if cand is None:
            continue

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
            bv = index.get_book(bid) or extra_query.get(bid)
            if bv is None:
                continue
            fb = fb_data.get(bid)

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

        def _desc(bid, bv):
            d = index.desc_of(bid)
            if d is None and bv is not None:
                d = bv.desc
            return d.astype(np.float32) if d is not None else None

        cand_desc = _desc(cid, cand)
        good_descs = [g for g in (_desc(bid, good_books[bid]) for bid in good_ids if bid in good_books) if g is not None]
        desc_score = float(max(float(np.dot(d, cand_desc)) for d in good_descs)) if (good_descs and cand_desc is not None) else 0.0

        if w_l1 != 0:
            good_l1s = [good_books[bid].l1.astype(np.float32) for bid in good_ids if bid in good_books]
            cand_l1 = cand.l1.astype(np.float32)
            l1_score = float(max(float(np.dot(l, cand_l1)) for l in good_l1s)) if good_l1s else 0.0
        else:
            l1_score = 0.0

        if w_l2 != 0:
            good_l2s = [good_books[bid].l2.astype(np.float32) for bid in good_ids if bid in good_books]
            cand_l2 = cand.l2.astype(np.float32)
            l2_score = float(max(float(np.dot(l, cand_l2)) for l in good_l2s)) if good_l2s else 0.0
        else:
            l2_score = 0.0

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
        tier = getattr(index, "_candidate_tier", {}).get(cid, "rich")
        pen = SOURCE_TIER_PENALTY.get(tier, 1.0)
        if pen != 1.0 and scores[cid] > 0:
            scores[cid] *= pen

    return scores
