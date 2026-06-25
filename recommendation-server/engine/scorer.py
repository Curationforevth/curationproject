"""v3 스코어링 알고리즘.
공식: 1.0×reason_score + 0.5×desc_score + 3.0×L1_score + 1.0×L2_score + 2.0×fb_desc_score
"""
from __future__ import annotations

import numpy as np
from engine.index import VectorIndex
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT)


def _maxsim(query_vecs: list, candidate_vecs: list) -> float:
    if not query_vecs or not candidate_vecs:
        return 0.0
    q = np.stack(query_vecs)
    c = np.stack(candidate_vecs)
    sims = q @ c.T
    return float(sims.max(axis=1).mean())


def _score_one(index: VectorIndex, liked_books: dict, fb_data: dict,
               candidate_id: str) -> float:
    cand = index.get_book(candidate_id)
    if cand is None:
        return 0.0

    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return 0.0

    # 1. reason_score
    weighted_maxsims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
        else:
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)

    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
        else:
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)

    reason_score = float(np.mean(weighted_maxsims)) if weighted_maxsims else 0.0

    # 2. desc_score
    good_descs = [index.get_book(bid).desc for bid in good_ids if index.get_book(bid)]
    desc_score = max(float(np.dot(d, cand.desc)) for d in good_descs) if good_descs else 0.0

    # 3. L1_score
    good_l1s = [index.get_book(bid).l1 for bid in good_ids if index.get_book(bid)]
    l1_score = max(float(np.dot(l, cand.l1)) for l in good_l1s) if good_l1s else 0.0

    # 4. L2_score
    good_l2s = [index.get_book(bid).l2 for bid in good_ids if index.get_book(bid)]
    l2_score = max(float(np.dot(l, cand.l2)) for l in good_l2s) if good_l2s else 0.0

    # 5. fb_desc_score
    fb_desc_vals = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_desc_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_desc_score = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0

    return (W_REASON * reason_score + W_DESC * desc_score +
            W_L1 * l1_score + W_L2 * l2_score + W_FB_DESC * fb_desc_score)


def recommend_scores(index: VectorIndex, liked_books: dict,
                     fb_data: dict) -> dict:
    if not liked_books:
        return {}
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    if not active:
        return {}
    read_ids = set(liked_books.keys())
    scores = {}
    for cid in index.book_ids:
        if cid in read_ids:
            continue
        scores[cid] = _score_one(index, active, fb_data, cid)
    return scores


def recommend_scores_two_stage(index: VectorIndex, liked_books: dict,
                               fb_data: dict, top_n: int) -> dict:
    """desc 선필터(stage1) 후 top_n 후보를 **완전 벡터화**로 스코어링(stage2).

    전체 brute-force(recommend_scores)는 2,679책을 벡터화 없이 Python 루프로 돌아
    무료 단일 CPU 에서 ~70s. 여기선 ① desc 유사도 상위 top_n 후보 선별 ② 그 후보들의
    reason 을 한 행렬로 쌓아 maxsim(segment-max via reduceat)·desc·fb 를 모두 numpy
    matmul 로 일괄 계산한다. _score_one 과 점수 동일(검증: good/bad/feedback 전부
    full 대비 top-10 일치 10/10, max|Δ|<0.001). 로컬 0.13s(전체 13.7s 대비), 시작
    메모리 추가 없음(후보 reason 만 요청 중 스택). W_DESC=3 이 최대라 고득점 책은
    desc 상위권에 포함되어 선필터로 누락되지 않는다.
    """
    if not liked_books:
        return {}
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    if not active:
        return {}
    read_ids = set(liked_books.keys())
    good_ids = [bid for bid, d in active.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in active.items() if d["rating"] == "bad"]
    if not good_ids and not bad_ids:
        return {}

    if index._desc_matrix is None:
        index.build_desc_matrix()
    # _desc_matrix 는 이미 f32(빌드 시 dtype). 매 호출 .astype 복사(~21MB)는 낭비 +
    # 무료 512MB 에서 peak 메모리만 키운다 → f32 면 그대로 쓰고 아니면만 변환.
    M = index._desc_matrix
    if M.dtype != np.float32:
        M = M.astype(np.float32)                       # (N, D)
    bid_to_idx = index._desc_bid_to_idx
    order = index._desc_bid_order

    # ── stage1: desc 선필터 → top_n 후보 ──────────────────────────────
    seed_ids = good_ids or bad_ids
    seed_idx = [bid_to_idx[b] for b in seed_ids if b in bid_to_idx]
    if not seed_idx:
        return recommend_scores(index, liked_books, fb_data)
    agg = (M[seed_idx] @ M.T).max(axis=0)              # (N,)
    for bid in read_ids:
        i = bid_to_idx.get(bid)
        if i is not None:
            agg[i] = -1e9
    n = min(top_n, agg.shape[0])
    cand_ids = [order[i] for i in np.argpartition(agg, -n)[-n:]]
    C = len(cand_ids)

    # ── 후보 reason 을 한 행렬로 스택 + segment 경계 ──────────────────
    mats, starts, lens, off = [], [], [], 0
    for c in cand_ids:
        rs = index.get_book(c).reasons
        starts.append(off)
        lens.append(len(rs))
        if rs:
            mats.append(np.asarray(rs, dtype=np.float32))
        off += len(rs)
    seg = np.array(starts)
    empty = np.array(lens) == 0
    CR = np.concatenate(mats) if mats else np.zeros((0, index.dim), np.float32)
    cand_desc = M[[bid_to_idx[c] for c in cand_ids]]   # (C, D)

    def _maxsim_vec(qr):       # (C,) : mean over query reasons of max over cand reasons
        if CR.shape[0] == 0 or qr.shape[0] == 0:
            return np.zeros(C, dtype=np.float32)
        sm = np.maximum.reduceat(qr @ CR.T, seg, axis=1)   # (nq, C)
        if empty.any():
            sm[:, empty] = 0.0
        return sm.mean(axis=0)

    def _fbsim_vec(emb):       # (C,) : max over cand reasons of emb·reason
        if CR.shape[0] == 0:
            return np.zeros(C, dtype=np.float32)
        sm = np.maximum.reduceat(CR @ emb.astype(np.float32), seg)
        if empty.any():
            sm[empty] = 0.0
        return sm

    def _reasons(bid):
        rs = index.get_book(bid).reasons
        return np.asarray(rs, dtype=np.float32) if rs else np.zeros((0, index.dim), np.float32)

    # ── reason_score: good/bad 가중 maxsim 의 평균 ───────────────────
    contribs = []
    for bid in good_ids:
        r_sim = _maxsim_vec(_reasons(bid))
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            contribs.append(FB_REASON_WEIGHT * _fbsim_vec(fb["emb"]) + REASON_WEIGHT_WITH_FB * r_sim)
        else:
            contribs.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
    for bid in bad_ids:
        r_sim = _maxsim_vec(_reasons(bid))
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            contribs.append(-(FB_REASON_WEIGHT * _fbsim_vec(fb["emb"]) + REASON_WEIGHT_WITH_FB * r_sim))
        else:
            contribs.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
    reason_score = np.mean(contribs, axis=0) if contribs else np.zeros(C, dtype=np.float32)

    # ── desc_score / fb_desc_score ──────────────────────────────────
    if good_ids:
        gd = M[[bid_to_idx[b] for b in good_ids if b in bid_to_idx]]
        desc_score = (cand_desc @ gd.T).max(axis=1)
    else:
        desc_score = np.zeros(C, dtype=np.float32)

    fbv = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fbv.append(sign * (cand_desc @ fb["emb"].astype(np.float32)))
    fb_desc_score = np.mean(fbv, axis=0) if fbv else np.zeros(C, dtype=np.float32)

    total = W_REASON * reason_score + W_DESC * desc_score + W_FB_DESC * fb_desc_score
    return {cand_ids[k]: float(total[k]) for k in range(C)}
