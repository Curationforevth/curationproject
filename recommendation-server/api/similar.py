from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import numpy as np

from auth import verify_jwt
from models import SimilarResponse, SimilarBook, SimilarUnionRequest
from engine.dedup import dedup_similar, filter_not_interested
from engine.cache import load_signals
from config import DEFAULT_SIMILAR_LIMIT, get_supabase

router = APIRouter()


def _ni_ids_for(user_id: str) -> set:
    """유저의 관심없음 book_id 집합 — 조회 실패 시 필터만 생략(응답은 계속).

    /similar 는 콘텐츠 유사도 표면이지만 유저에게는 '추천'으로 읽힌다 —
    관심없음 책이 여기서 재등장하면 신호가 무시된 것(2026-07-03 QA 결함 1).
    """
    try:
        signals = load_signals(get_supabase(), user_id) or []
        return {s["book_id"] for s in signals
                if s.get("signal") == "not_interested"}
    except Exception:
        return set()


def _build_similar_books(results, books_meta) -> list[SimilarBook]:
    out: list[SimilarBook] = []
    for bid, score in results:
        meta = books_meta.get(bid, {})
        out.append(SimilarBook(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""), author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        ))
    return out


@router.get("/similar/{book_id}", response_model=SimilarResponse)
async def get_similar(
    book_id: str,
    request: Request,
    limit: int = Query(DEFAULT_SIMILAR_LIMIT, ge=1, le=50),
    uid: str = Depends(verify_jwt),
):
    index = request.app.state.index
    books_meta = request.app.state.books_meta

    if index.get_book(book_id) is None:
        raise HTTPException(404, f"Book {book_id} not found in index")

    # over-fetch 후 관심없음 제거 → 시드의 다른 판본·중복 판본을 접어 limit 개 반환.
    raw = index.similar_by_desc(book_id, limit=limit * 2 + 5)
    raw = filter_not_interested(raw, _ni_ids_for(uid))
    results = dedup_similar(raw, books_meta, book_id, limit)
    return SimilarResponse(book_id=book_id, similar=_build_similar_books(results, books_meta))


@router.post("/similar/union", response_model=SimilarResponse)
async def similar_union(
    payload: SimilarUnionRequest,
    request: Request,
    uid: str = Depends(verify_jwt),
):
    """Average the desc embeddings of the supplied book_ids and return
    top-K nearest books, excluding the inputs themselves.

    Books not present in the index are silently skipped.
    Returns an empty similar list if no input books exist in the index.
    """
    index = request.app.state.index
    books_meta = request.app.state.books_meta
    limit = max(1, min(50, payload.limit))

    vectors = []
    seed_ids = set()
    for bid in payload.book_ids:
        # desc_of: strip(dedup) 인덱스에선 per-book desc=None → _desc_matrix 에서 조회.
        desc = index.desc_of(bid)
        if desc is None:
            continue
        vectors.append(desc)
        seed_ids.add(bid)

    if not vectors:
        return SimilarResponse(book_id="union", similar=[])

    avg = np.mean(np.stack(vectors), axis=0)
    norm = float(np.linalg.norm(avg))
    if norm == 0:
        return SimilarResponse(book_id="union", similar=[])
    avg = avg / norm

    # 시드 자신 + 관심없음 책 제외 (관심없음 재등장 방지 — get_similar 와 동일).
    results = index.similar_by_vector(
        avg, exclude_ids=seed_ids | _ni_ids_for(uid), limit=limit)
    return SimilarResponse(book_id="union", similar=_build_similar_books(results, books_meta))
