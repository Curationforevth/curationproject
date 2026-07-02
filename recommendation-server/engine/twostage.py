"""Two-stage 추천 엔진. Stage 1 (후보 선별) + Stage 2 (정밀 스코어링).

Phase 2 (2026-07-02) 벡터화 — 스코어링 수식·가중치·후보 선정 의미 무변경,
수학적 동치 변환만(float 합산 순서 변화). 동등성은 tests/test_twostage_equivalence.py
(vs engine/twostage_reference.py 기준선) + scripts/verify_equivalence.py 로 증명.
- stage1: 선형항(pb/fb)들을 항별 matvec 루프 대신 단일 결합 쿼리벡터로 접음
  (Σ coef·(M@q) = M@(Σ coef·q)) — 블록당 GEMM 2회 + matvec 3회로 고정.
- stage2: 후보×쿼리책 이중 Python 루프(쿼리 reason 을 후보마다 재업캐스트)를
  concat + np.maximum.reduceat 세그먼트 연산으로 대체(scorer.py v3 경로에서 검증된 패턴).
"""
from __future__ import annotations

import numpy as np

from engine.index import VectorIndex
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT, SOURCE_TIER_PENALTY)

# stage1 의 f32 업캐스트(dm/am)를 행블록으로 처리해 요청당 transient 를 O(block)으로
# 고정(N 무관). 무료 512MB 에서 후보풀이 커져도 OOM 안 나게 하는 핵심(2026-06-29 OOM).
# f16→f32 는 무손실이고 모든 reduction 이 행단위라 블록 처리는 전체 처리와 bit-identical.
STAGE1_CHUNK = 1024

# stage2 후보 블록 크기 — 후보 reasons f32 concat(CR)이 top_n 에 비례해 커지는 걸
# O(block)으로 고정. 후보풀은 reason-rich 책으로 편향돼(평균 4.7개 대비 ~15개)
# top_n=700 무분할 시 transient ~175MB(실측) → 348MB 상주와 합쳐 512MB 초과 위험.
# 후보별 점수는 상호 독립이라 블록 분할은 결과 동일(BLAS 커널 블로킹 차이로
# 마지막 ulp ~1e-7 만 허용 — 불변성 테스트로 고정).
STAGE2_CHUNK = 150


def stage1_hybrid(
    liked_books: dict,
    fb_data: dict,
    desc_matrix_f16: np.ndarray,
    agg_reason_matrix_f16: np.ndarray,
    bid_order: list[str],
    top_n: int = 700,
    extra_query: dict | None = None,
) -> list[str]:
    # extra_query: 정적 인덱스(bid_order) 밖 좋/싫 책의 BookVectors. desc 만 query 로
    # 주입한다(fb 는 아래 fb_data 루프가 무가드로 이미 반영 — 이중계산 금지).
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

    # ── 쿼리 벡터는 작아서 1회만 f32 업캐스트(블록 루프 밖). dm/am 전체는 업캐스트 안 함 ──
    # good_descs/good_aggs: 인덱스 hit + 주입책(desc, agg=zero) 결합
    idx_descs = _rows_f32(desc_matrix_f16, good_desc_indices)
    idx_aggs = _rows_f32(agg_reason_matrix_f16, good_desc_indices)
    if extra_good:
        ex_descs = np.stack([extra_query[b].desc.astype(np.float32) for b in extra_good])
        ex_aggs = np.zeros((len(extra_good), D), np.float32)  # 유저 책 reason 없음 → 0
        good_descs = np.vstack([idx_descs, ex_descs])
        good_aggs = np.vstack([idx_aggs, ex_aggs])
    else:
        good_descs = idx_descs
        good_aggs = idx_aggs
    good_descs_T = good_descs.T
    good_aggs_T = good_aggs.T

    # sq_fb 항: 비-neutral 피드백 (sign, emb_f32)
    fb_terms = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_terms.append((sign, fb["emb"].astype(np.float32)))

    # pb 항: desc-공간 (coef, q) / agg-공간 (coef, q). dm[idx]/am[idx] 는 f16 슬라이스를 1회 업캐스트.
    pb_desc_terms = []  # (coef, q_f32)
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
    # 주입책 desc 항(인덱스 밖). fb 항은 아래 fb_terms 가 무가드로 반영 — 이중계산 금지.
    for bid in extra_good:
        pb_desc_terms.append((3.0, extra_query[bid].desc.astype(np.float32)))
    for bid in extra_bad:
        pb_desc_terms.append((-1.5, extra_query[bid].desc.astype(np.float32)))
    for sign, emb in fb_terms:
        pb_desc_terms.append((sign * 2.0, emb))

    # ── 선형항 사전 결합: Σ coef·(M @ q) = M @ (Σ coef·q) — 항별 matvec 루프가
    # 블록마다 (2G+B+F)회 메모리 패스를 도는 게 진짜 병목이라, 결합벡터 3개로 접어
    # 블록당 GEMM 2회(max용) + matvec ≤3회로 고정한다. 수식 동치(합산 순서만 변화).
    q_fb = None
    if fb_terms:
        q_fb = np.zeros(D, np.float32)
        for sign, emb in fb_terms:
            q_fb += sign * emb
    q_pb_desc = None
    if pb_desc_terms:
        q_pb_desc = np.zeros(D, np.float32)
        for coef, q in pb_desc_terms:
            q_pb_desc += coef * q
    q_pb_agg = None
    if pb_agg_terms:
        q_pb_agg = np.zeros(D, np.float32)
        for coef, q in pb_agg_terms:
            q_pb_agg += coef * q

    # ── 행블록 루프: dm/am 을 블록 단위로만 f32 업캐스트(transient = O(block)) ──
    sq_scores = np.empty(N, dtype=np.float32)
    pb_scores = np.zeros(N, dtype=np.float32)
    for s in range(0, N, STAGE1_CHUNK):
        e = min(s + STAGE1_CHUNK, N)
        dm_blk = desc_matrix_f16[s:e].astype(np.float32)
        am_blk = agg_reason_matrix_f16[s:e].astype(np.float32)
        b = e - s

        sq_desc_blk = (dm_blk @ good_descs_T).max(axis=1)
        sq_reason_blk = (am_blk @ good_aggs_T).max(axis=1) if good_aggs.shape[0] else np.zeros(b, np.float32)
        sq_fb_blk = (dm_blk @ q_fb) if q_fb is not None else np.zeros(b, dtype=np.float32)
        sq_scores[s:e] = 3.0 * sq_desc_blk + 2.0 * sq_reason_blk + 2.0 * sq_fb_blk

        pb_blk = np.zeros(b, dtype=np.float32)
        if q_pb_desc is not None:
            pb_blk += dm_blk @ q_pb_desc
        if q_pb_agg is not None:
            pb_blk += am_blk @ q_pb_agg
        pb_scores[s:e] = pb_blk

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
    extra_query: dict | None = None,
) -> dict:
    """Stage 2 정밀 스코어링 — 후보를 STAGE2_CHUNK 블록으로 나눠 벡터화 스코어링.

    후보별 점수는 상호 독립이라 블록 분할은 무분할과 bit-identical. 분할 이유는
    메모리(STAGE2_CHUNK 주석) — 쿼리측 준비(작음)만 블록마다 반복된다.
    """
    scores: dict = {}
    for s in range(0, len(candidate_ids), STAGE2_CHUNK):
        scores.update(_batch_score_block(
            index, liked_books, fb_data, candidate_ids[s:s + STAGE2_CHUNK],
            prestacked_reasons, w_reason=w_reason, w_desc=w_desc,
            w_l1=w_l1, w_l2=w_l2, w_fb_desc=w_fb_desc, extra_query=extra_query))
    return scores


def _batch_score_block(
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
    extra_query: dict | None = None,
) -> dict:
    """한 후보 블록의 완전 벡터화 스코어링 (batch_score_prestacked 가 감쌈).

    기준선(twostage_reference.batch_score_prestacked_reference)과 점수 동일
    (max|Δ|<1e-4, float 합산 순서 기인). 과거 구현은 후보 C × 쿼리책 (G+B) 이중
    Python 루프에서 같은 쿼리 reason 을 후보마다 f16→f32 재업캐스트(C×(G+B)회,
    prod 150×~25 ≈ 수천 회)했다 — 이게 stage2 병목. 여기선 후보 reasons 를 1회
    concat 하고 쿼리책마다 GEMM 1회 + np.maximum.reduceat 세그먼트-max 로 전 후보를
    일괄 계산한다. transient = 후보 reasons f32 1벌(150후보 기준 ~12MB, 512MB 안전).

    extra_query: 정적 인덱스 밖 좋/싫 책의 BookVectors(desc 기반 취향 주입).
    """
    extra_query = extra_query or {}
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return {}

    # 좋아요 책 벡터를 미리 수집 (인덱스 → 없으면 주입책)
    good_books = {bid: (index.get_book(bid) or extra_query.get(bid)) for bid in good_ids}
    good_books = {bid: bv for bid, bv in good_books.items() if bv is not None}

    # 인덱스에 없는 후보는 제외(기준선과 동일), 순서 유지
    cands = [(cid, index.get_book(cid)) for cid in candidate_ids]
    cands = [(cid, bv) for cid, bv in cands if bv is not None]
    if not cands:
        return {}
    C = len(cands)
    dim = index.dim

    def _reasons_f32(bid, bv):
        pr = prestacked_reasons.get(bid)
        if pr is not None and pr.shape[0] > 0:
            return pr.astype(np.float32)
        if bv is not None and bv.reasons:
            return np.stack(bv.reasons).astype(np.float32)
        return None

    # ── 후보 reasons concat + 세그먼트 경계 (f32 업캐스트는 후보당 1회) ──
    mats, lens = [], []
    for cid, bv in cands:
        r = _reasons_f32(cid, bv)
        if r is None:
            lens.append(0)
        else:
            lens.append(r.shape[0])
            mats.append(r)
    lens = np.asarray(lens)
    seg = np.concatenate(([0], np.cumsum(lens)[:-1]))
    empty = lens == 0
    CR = np.concatenate(mats) if mats else np.zeros((0, dim), np.float32)
    total_r = CR.shape[0]
    # reduceat 인덱스는 < len 이어야 한다. 말미 빈 세그먼트는 start==total_r 라
    # 그대로 넘기면 IndexError, 마지막 유효 인덱스로 클램프하면 **직전 세그먼트가
    # [start, total_r-1) 로 좁아져 마지막 reason 이 max 에서 누락**된다(동등성 테스트로
    # 검출). → 말미 빈 세그먼트(seg==total_r)는 reduceat 에서 아예 제외하고 0 으로
    # 채운다. 중간 빈 세그먼트(start<total_r)는 reduceat 결과가 무의미 값이므로
    # empty 마스크로 0 덮어씀(기준선의 r_sim=0.0 과 동일).
    n_lead = int(np.searchsorted(seg, total_r, side="left"))  # 말미 빈 세그먼트 시작 위치

    def _maxsim_vec(qr):
        """(C,) — 쿼리 reason 별 후보 reason max 의 평균(기준선 r_sim 과 동일)."""
        if total_r == 0 or qr is None or qr.shape[0] == 0:
            return np.zeros(C, dtype=np.float32)
        sm = np.zeros((qr.shape[0], C), dtype=np.float32)
        sm[:, :n_lead] = np.maximum.reduceat(qr @ CR.T, seg[:n_lead], axis=1)
        if empty.any():
            sm[:, empty] = 0.0
        return sm.mean(axis=0)

    def _fbsim_vec(emb_f32):
        """(C,) — 후보 reason 중 fb 임베딩과의 max(기준선 fb_sim 과 동일)."""
        if total_r == 0:
            return np.zeros(C, dtype=np.float32)
        sm = np.zeros(C, dtype=np.float32)
        sm[:n_lead] = np.maximum.reduceat(CR @ emb_f32, seg[:n_lead])
        if empty.any():
            sm[empty] = 0.0
        return sm

    # ── 1. reason_score: good/bad 가중 maxsim 의 평균 (쿼리 reason 업캐스트 책당 1회) ──
    contribs = []
    for bid in good_ids:
        bv = good_books.get(bid)
        if bv is None:
            continue
        r_sim = _maxsim_vec(_reasons_f32(bid, bv))
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            fb_sim = _fbsim_vec(fb["emb"].astype(np.float32))
            contribs.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
        else:
            contribs.append(REASON_WEIGHT_WITHOUT_FB * r_sim)
    for bid in bad_ids:
        bv = index.get_book(bid) or extra_query.get(bid)
        if bv is None:
            continue
        r_sim = _maxsim_vec(_reasons_f32(bid, bv))
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = _fbsim_vec(fb["emb"].astype(np.float32))
            contribs.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
        else:
            contribs.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)
    reason_score = np.mean(contribs, axis=0) if contribs else np.zeros(C, dtype=np.float32)

    # ── 2. desc_score ──
    # desc 는 per-book(BookVectors.desc) 또는 strip 시 index._desc_matrix 에서 조회
    # (메모리 dedup: desc 1벌). 인덱스 밖 주입책(extra_query)은 per-book desc 사용.
    def _desc_f32(bid, bv):
        d = index.desc_of(bid)
        if d is None and bv is not None:
            d = bv.desc
        return d.astype(np.float32) if d is not None else None

    zero_d = np.zeros(dim, dtype=np.float32)
    cd_rows = [_desc_f32(cid, bv) for cid, bv in cands]
    CD = np.stack([d if d is not None else zero_d for d in cd_rows])   # (C, D)

    good_desc_rows = [g for g in (_desc_f32(bid, good_books[bid])
                                  for bid in good_ids if bid in good_books) if g is not None]
    if good_desc_rows:
        desc_score = (CD @ np.stack(good_desc_rows).T).max(axis=1)
    else:
        desc_score = np.zeros(C, dtype=np.float32)

    # ── 3/4. l1_score / l2_score (w=0이면 skip — prod 기본) ──
    if w_l1 != 0:
        good_l1s = [good_books[bid].l1.astype(np.float32) for bid in good_ids if bid in good_books]
        if good_l1s:
            CL1 = np.stack([bv.l1.astype(np.float32) for _, bv in cands])
            l1_score = (CL1 @ np.stack(good_l1s).T).max(axis=1)
        else:
            l1_score = np.zeros(C, dtype=np.float32)
    else:
        l1_score = np.zeros(C, dtype=np.float32)

    if w_l2 != 0:
        good_l2s = [good_books[bid].l2.astype(np.float32) for bid in good_ids if bid in good_books]
        if good_l2s:
            CL2 = np.stack([bv.l2.astype(np.float32) for _, bv in cands])
            l2_score = (CL2 @ np.stack(good_l2s).T).max(axis=1)
        else:
            l2_score = np.zeros(C, dtype=np.float32)
    else:
        l2_score = np.zeros(C, dtype=np.float32)

    # ── 5. fb_desc_score: mean(sign·(CD@emb)) = CD @ (Σ sign·emb / n) — 선형 결합 ──
    fb_entries = [(-1.0 if fb["is_dislike"] else 1.0, fb["emb"])
                  for bid, fb in fb_data.items()
                  if liked_books.get(bid, {}).get("rating") != "neutral"]
    if fb_entries:
        q_fb = np.zeros(dim, dtype=np.float32)
        for sign, emb in fb_entries:
            q_fb += sign * emb.astype(np.float32)
        fb_desc_score = CD @ (q_fb / len(fb_entries))
    else:
        fb_desc_score = np.zeros(C, dtype=np.float32)

    total = (w_reason * reason_score
             + w_desc * desc_score
             + w_l1 * l1_score
             + w_l2 * l2_score
             + w_fb_desc * fb_desc_score)

    # source_tier 차등 down-weight (positive-part — 음수 점수는 미변경해
    # 0쪽으로 올려 랭크가 상승하는 버그 방지, B1). 페널티는 후보에만(쿼리책 무관).
    tier_map = getattr(index, "_candidate_tier", {})
    pen = np.array([SOURCE_TIER_PENALTY.get(tier_map.get(cid, "rich"), 1.0)
                    for cid, _ in cands], dtype=np.float32)
    total = np.where((pen != 1.0) & (total > 0), total * pen, total)

    return {cid: float(total[k]) for k, (cid, _) in enumerate(cands)}
