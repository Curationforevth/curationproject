from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from auth import verify_jwt
from models import RecommendResponse, BookScore
from engine.scorer import recommend_scores
from engine.twostage import stage1_hybrid, batch_score_prestacked
from engine.utils import to_np
from engine.cache import compute_input_hash, load_cache, save_cache_if_current
from config import DEFAULT_RECOMMEND_LIMIT, STAGE1_TOP_N, CACHE_TOP_N, get_supabase

router = APIRouter()


@router.get("/recommend/{user_id}", response_model=RecommendResponse)
async def get_recommendations(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    limit: int = Query(DEFAULT_RECOMMEND_LIMIT, ge=1, le=50),
    current_user: str = Depends(verify_jwt),
):
    if current_user != user_id:
        raise HTTPException(403, "Cannot access other user's recommendations")

    index = request.app.state.index
    books_meta = request.app.state.books_meta

    sb = get_supabase()
    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding"
    ).eq("user_id", user_id).execute()

    if not ub_res.data:
        return RecommendResponse(
            user_id=user_id, recommendations=[],
            meta={"total_liked": 0, "total_disliked": 0, "has_feedback": False},
        )

    # -----------------------------------------------------------------------
    # 캐시 확인
    # -----------------------------------------------------------------------
    input_hash = compute_input_hash(ub_res.data)
    cache = load_cache(user_id)
    if (
        cache
        and cache.get("input_hash") == input_hash
        and cache.get("computed_at", "") > (getattr(request.app.state, "built_at", None) or "")
    ):
        cached_recs = cache["recommendations"][:limit]
        recs = [BookScore(**r) for r in cached_recs]
        return RecommendResponse(
            user_id=user_id, recommendations=recs,
            meta={
                "total_liked": cache.get("good_count", 0),
                "total_disliked": cache.get("bad_count", 0),
                "has_feedback": cache.get("has_feedback", False),
                "from_cache": True,
            },
        )

    # -----------------------------------------------------------------------
    # 온디맨드 계산
    # -----------------------------------------------------------------------
    liked_books: dict = {}
    fb_data: dict = {}
    total_liked = total_disliked = 0
    has_feedback = False

    for ub in ub_res.data:
        bid = ub["book_id"]
        rating = ub.get("rating", "neutral")
        liked_books[bid] = {"rating": rating}
        if rating == "good":
            total_liked += 1
        elif rating == "bad":
            total_disliked += 1
        fb_emb = ub.get("feedback_embedding")
        if fb_emb:
            has_feedback = True
            fb_data[bid] = {"emb": to_np(fb_emb), "is_dislike": rating == "bad"}

    prestacked = request.app.state.prestacked_reasons
    if prestacked is not None:
        # v4 two-stage
        candidates = stage1_hybrid(
            liked_books, fb_data,
            request.app.state.desc_matrix_f16,
            request.app.state.agg_reason_matrix_f16,
            request.app.state.bid_order,
            top_n=STAGE1_TOP_N,
        )
        scores = batch_score_prestacked(
            index, liked_books, fb_data, candidates, prestacked)
    else:
        # v3 fallback: brute-force scoring
        scores = recommend_scores(index, liked_books, fb_data)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:CACHE_TOP_N]

    recs = []
    recs_for_cache: list[dict] = []
    for bid, score in sorted_scores:
        meta = books_meta.get(bid)
        if meta is None:
            # ghost book defense: book in index but not in meta — skip
            continue
        book = BookScore(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""), author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        )
        recs.append(book)
        recs_for_cache.append(book.dict())

    # 백그라운드에서 캐시 저장
    background_tasks.add_task(
        save_cache_if_current,
        user_id,
        recs_for_cache,
        input_hash,
        total_liked,
        total_disliked,
        has_feedback,
    )

    return RecommendResponse(
        user_id=user_id, recommendations=recs[:limit],
        meta={"total_liked": total_liked, "total_disliked": total_disliked,
              "has_feedback": has_feedback},
    )
