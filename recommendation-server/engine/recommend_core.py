"""recommendation-server/engine/recommend_core.py

api/recommend.py 의 scoring 로직을 추출. api 계층(HTTP/cache)과 분리하여
/home 에서도 재사용 가능하도록 한다.

반환: sorted by score desc, 최대 CACHE_TOP_N 길이의 (book_id, score) 리스트.
응답 조립(meta 조회, BookScore dict 변환)은 호출자에서 수행.
"""
from __future__ import annotations
import asyncio
from typing import Optional

from engine.scorer import recommend_scores_two_stage
from engine.twostage import stage1_hybrid, batch_score_prestacked
from config import STAGE1_TOP_N, CACHE_TOP_N


# 무료 512MB 메모리 가드: Tier2 스코어링은 후보 reason 임시할당(~70MB/건)이 있어
# 동시 다발이면 OOM. 동시 inline 계산을 2건으로 제한(2*70+인덱스277 ≈ 417MB < 512).
# 슬롯이 없으면 호출측이 백그라운드 재계산 + fallback 으로 우회(첫화면 안 막힘).
_COMPUTE_SEM = asyncio.Semaphore(2)


async def try_compute_inline(app_state, liked_books: dict, fb_data: dict,
                             extra_query: Optional[dict] = None):
    """슬롯 있으면 스레드에서 즉시 스코어링해 결과 반환, 없으면 None.

    None 이면 호출측은 백그라운드 재계산을 트리거하고 fallback 을 반환해야 한다.
    extra_query: 이미 임베딩된 인덱스 밖 책의 BookVectors(OpenAI 없이 호출측이 resolve).
    """
    try:
        await asyncio.wait_for(_COMPUTE_SEM.acquire(), timeout=0.01)
    except asyncio.TimeoutError:
        return None
    try:
        return await asyncio.to_thread(
            compute_scored_books,
            index=app_state.index,
            liked_books=liked_books,
            fb_data=fb_data,
            prestacked_reasons=app_state.prestacked_reasons,
            desc_matrix_f16=app_state.desc_matrix_f16,
            agg_reason_matrix_f16=app_state.agg_reason_matrix_f16,
            bid_order=app_state.bid_order,
            extra_query=extra_query,
        )
    finally:
        _COMPUTE_SEM.release()


def compute_scored_books(
    *,
    index,
    liked_books: dict,
    fb_data: dict,
    prestacked_reasons: Optional[dict],
    desc_matrix_f16,
    agg_reason_matrix_f16,
    bid_order: list,
    extra_query: Optional[dict] = None,
) -> list[tuple[str, float]]:
    """
    Score all candidate books for a user and return top-N.

    - prestacked_reasons 있으면 v4 two-stage (stage1_hybrid + batch_score_prestacked)
    - 없으면 v3 fallback (recommend_scores — full index brute force)

    Empty inputs (no likes) → empty list.
    """
    if not liked_books and not fb_data:
        return []

    if prestacked_reasons is not None:
        candidates = stage1_hybrid(
            liked_books, fb_data,
            desc_matrix_f16, agg_reason_matrix_f16, bid_order,
            top_n=STAGE1_TOP_N,
            extra_query=extra_query,
        )
        scores = batch_score_prestacked(
            index, liked_books, fb_data, candidates, prestacked_reasons,
            extra_query=extra_query)
    else:
        # v3 — desc 선필터 two-stage (전체 brute-force 는 단일워커 ~13s 블로킹).
        scores = recommend_scores_two_stage(index, liked_books, fb_data, top_n=STAGE1_TOP_N)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:CACHE_TOP_N]
