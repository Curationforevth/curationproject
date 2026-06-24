"""recommendation-server/api/curation.py

/curations/{curation_id}/books — 큐레이션의 전체 책 리스트 페이징.
Flutter '더보기' 탭용.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import verify_jwt
from config import get_supabase

router = APIRouter()


@router.get("/curations/{curation_id}/books")
async def get_curation_books(
    curation_id: int,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    _: str = Depends(verify_jwt),
):
    sb = get_supabase()

    t_res = sb.table("curation_themes").select(
        "id,theme_type,title,description"
    ).eq("id", curation_id).eq("is_active", True).limit(1).execute()
    theme = t_res.data[0] if t_res.data else None
    if not theme:
        raise HTTPException(404, "Curation not found or inactive")

    c_res = sb.table("curation_cache").select(
        "book_ids,cached_at"
    ).eq("curation_id", curation_id).limit(1).execute()
    cache = c_res.data[0] if c_res.data else None
    if not cache:
        return {
            "curation_id": curation_id,
            "theme_type": theme["theme_type"],
            "title": theme["title"],
            "description": theme.get("description"),
            "total": 0, "offset": offset, "limit": limit,
            "books": [], "cached_at": None,
        }

    all_book_ids: list[str] = cache["book_ids"]
    total = len(all_book_ids)
    page_ids = all_book_ids[offset:offset + limit]

    books_meta = request.app.state.books_meta
    books = []
    for bid in page_ids:
        meta = books_meta.get(bid)
        if meta is None:
            continue
        books.append({
            "book_id": bid,
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "cover_url": meta.get("cover_url"),
        })

    return {
        "curation_id": curation_id,
        "theme_type": theme["theme_type"],
        "title": theme["title"],
        "description": theme.get("description"),
        "total": total, "offset": offset, "limit": limit,
        "books": books,
        "cached_at": cache["cached_at"],
    }
