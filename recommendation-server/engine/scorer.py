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
    """desc 선필터(stage1) 후 top_n 후보만 정확 스코어링(stage2).

    전체 brute-force(recommend_scores)는 2,679책 × likes 를 벡터화 없이 Python
    루프로 돌아 ~13s 걸려 단일워커(무료티어)를 블로킹 → /health 5s timeout →
    Render 재시작. desc 유사도 상위 top_n 만 후보로 두면 동일 top 결과를 ~5x 빠르게
    얻는다(검증: STAGE1_TOP_N=500~700 에서 full 대비 top-10 일치 10/10). desc
    가중치(W_DESC=3)가 최대라 고득점 책은 desc 상위권에 반드시 포함된다.
    stage2 의 _score_one 은 reason/desc/fb 를 그대로 계산하므로 후보 내 랭킹은 정확.
    """
    if not liked_books:
        return {}
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    if not active:
        return {}
    read_ids = set(liked_books.keys())

    good_ids = [bid for bid, d in active.items() if d["rating"] == "good"]
    good_descs = [index.get_book(b).desc for b in good_ids if index.get_book(b) is not None]
    if not good_descs:
        # good desc 없음(드묾, dislike만) → 전체 fallback (정확성 우선)
        return recommend_scores(index, liked_books, fb_data)

    if index._desc_matrix is None:
        index.build_desc_matrix()
    G = np.stack(good_descs)                          # (g, D)
    agg = (G @ index._desc_matrix.T).max(axis=0)      # (B,) 후보별 best desc 유사도
    for bid in read_ids:
        i = index._desc_bid_to_idx.get(bid)
        if i is not None:
            agg[i] = -1e9
    n = min(top_n, agg.shape[0])
    cand_idx = np.argpartition(agg, -n)[-n:]
    order = index._desc_bid_order

    scores = {}
    for i in cand_idx:
        cid = order[i]
        if cid in read_ids:
            continue
        scores[cid] = _score_one(index, active, fb_data, cid)
    return scores
