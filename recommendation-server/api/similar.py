from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import numpy as np

from auth import verify_jwt
from models import SimilarResponse, SimilarBook, SimilarUnionRequest
from config import DEFAULT_SIMILAR_LIMIT

router = APIRouter()


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
    _: str = Depends(verify_jwt),
):
    index = request.app.state.index
    books_meta = request.app.state.books_meta

    if index.get_book(book_id) is None:
        raise HTTPException(404, f"Book {book_id} not found in index")

    results = index.similar_by_desc(book_id, limit=limit)
    return SimilarResponse(book_id=book_id, similar=_build_similar_books(results, books_meta))


@router.post("/similar/union", response_model=SimilarResponse)
async def similar_union(
    payload: SimilarUnionRequest,
    request: Request,
    _: str = Depends(verify_jwt),
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
        bv = index.get_book(bid)
        if bv is None:
            continue
        vectors.append(bv.desc)
        seed_ids.add(bid)

    if not vectors:
        return SimilarResponse(book_id="union", similar=[])

    avg = np.mean(np.stack(vectors), axis=0)
    norm = float(np.linalg.norm(avg))
    if norm == 0:
        return SimilarResponse(book_id="union", similar=[])
    avg = avg / norm

    results = index.similar_by_vector(avg, exclude_ids=seed_ids, limit=limit)
    return SimilarResponse(book_id="union", similar=_build_similar_books(results, books_meta))
