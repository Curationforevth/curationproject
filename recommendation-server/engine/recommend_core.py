"""recommendation-server/engine/recommend_core.py

api/recommend.py 의 scoring 로직을 추출. api 계층(HTTP/cache)과 분리하여
/home 에서도 재사용 가능하도록 한다.

반환: sorted by score desc, 최대 CACHE_TOP_N 길이의 (book_id, score) 리스트.
응답 조립(meta 조회, BookScore dict 변환)은 호출자에서 수행.
"""
from __future__ import annotations
from typing import Optional

from engine.scorer import recommend_scores
from engine.twostage import stage1_hybrid, batch_score_prestacked
from config import STAGE1_TOP_N, CACHE_TOP_N


def compute_scored_books(
    *,
    index,
    liked_books: dict,
    fb_data: dict,
    prestacked_reasons: Optional[dict],
    desc_matrix_f16,
    agg_reason_matrix_f16,
    bid_order: list,
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
        )
        scores = batch_score_prestacked(
            index, liked_books, fb_data, candidates, prestacked_reasons)
    else:
        scores = recommend_scores(index, liked_books, fb_data)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:CACHE_TOP_N]
