from fastapi import APIRouter, Depends, HTTPException, Query
from auth import verify_jwt
from models import SimilarResponse, SimilarBook
from config import DEFAULT_SIMILAR_LIMIT

router = APIRouter()


@router.get("/similar/{book_id}", response_model=SimilarResponse)
async def get_similar(
    book_id: str,
    limit: int = Query(DEFAULT_SIMILAR_LIMIT, ge=1, le=50),
    _: str = Depends(verify_jwt),
):
    from main import app_state
    index = app_state["index"]
    books_meta = app_state["books_meta"]

    if index.get_book(book_id) is None:
        raise HTTPException(404, f"Book {book_id} not found in index")

    results = index.similar_by_desc(book_id, limit=limit)
    similar = []
    for bid, score in results:
        meta = books_meta.get(bid, {})
        similar.append(SimilarBook(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""), author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        ))

    return SimilarResponse(book_id=book_id, similar=similar)
