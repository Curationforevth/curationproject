from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from auth import verify_jwt
from models import RecommendResponse, BookScore
from engine.cache import (compute_input_hash, load_cache,
                          recompute_recommendations, save_cache_if_current)
from engine.recommend_core import try_compute_inline
from engine.user_embed import resolve_extra_query_vectors
from engine.dedup import dedup_by_work
from engine.utils import to_np
from config import DEFAULT_RECOMMEND_LIMIT, get_supabase

router = APIRouter()


def _dedup_cached(rows: list, limit: int) -> list:
    """캐시된 추천(dict)에서 같은 작품의 다른 판본을 접고 limit 개로 자른다.
    (인덱스의 0.8% 가 중복 판본 — serving 에서만 접고 캐시/스코어는 불변.)"""
    deduped = dedup_by_work(rows, lambda r: (r.get("title", ""), r.get("author", "")))
    return [BookScore(**r) for r in deduped[:limit]]


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

    # Phase 1B — Tier 체크 먼저. Tier 0/1 은 feedback_embedding 조회 생략 (I1)
    us_res = sb.table("user_state").select("current_tier").eq("user_id", user_id).limit(1).execute()
    current_tier = (us_res.data[0]["current_tier"] if us_res.data else 0)
    if current_tier < 2:
        # rating 만 조회 — feedback_embedding 불필요
        ub_res = sb.table("user_books").select("rating").eq("user_id", user_id).execute()
        data = ub_res.data or []
        if not data:
            return RecommendResponse(
                user_id=user_id, recommendations=[],
                meta={"total_liked": 0, "total_disliked": 0, "has_feedback": False},
            )
        return RecommendResponse(
            user_id=user_id,
            recommendations=[],
            meta={
                "total_liked": sum(1 for r in data if r.get("rating") == "good"),
                "total_disliked": sum(1 for r in data if r.get("rating") == "bad"),
                "has_feedback": False,  # Tier 0/1 에선 어차피 사용 안 하므로 False
                "tier": current_tier,
                "reason": "insufficient_likes",
            },
        )

    # Tier 2 — feedback_embedding + 트리거 술어용 emotion_tags/review_text 포함 fetch
    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding,emotion_tags,review_text"
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

    if cache:
        # 캐시 히트: hash 일치 + 인덱스 빌드 이후 계산된 것
        if (cache.get("input_hash") == input_hash
                and cache.get("computed_at", "") > (getattr(request.app.state, "built_at", None) or "")):
            recs = _dedup_cached(cache["recommendations"], limit)
            return RecommendResponse(
                user_id=user_id, recommendations=recs,
                meta={
                    "total_liked": cache.get("good_count", 0),
                    "total_disliked": cache.get("bad_count", 0),
                    "has_feedback": cache.get("has_feedback", False),
                    "from_cache": True,
                },
            )

        # C3: 백그라운드 재계산 진행 중이면 stale 캐시라도 반환 (중복 계산 방지)
        if cache.get("computing") and cache.get("recommendations"):
            recs = _dedup_cached(cache["recommendations"], limit)
            return RecommendResponse(
                user_id=user_id, recommendations=recs,
                meta={
                    "total_liked": cache.get("good_count", 0),
                    "total_disliked": cache.get("bad_count", 0),
                    "has_feedback": cache.get("has_feedback", False),
                    "from_cache": True, "stale": True,
                },
            )

    # -----------------------------------------------------------------------
    # 캐시 미스 — 요청경로에서 스코어링하지 않는다.
    #
    # 무료티어 단일 CPU 에선 전체 스코어링이 ~70s 라(로컬 3.9s 의 ~18배) 요청을
    # 막으면 사실상 타임아웃. 대신 백그라운드 재계산을 트리거하고 즉시 반환한다
    # (stale 캐시 있으면 그것, 없으면 빈 결과 + computing). 다음 호출에서 캐시
    # 히트로 빠르게 받는다. 재계산은 recompute_recommendations 가 two-stage 로 수행.
    # -----------------------------------------------------------------------
    total_liked = sum(1 for ub in ub_res.data if ub.get("rating") == "good")
    total_disliked = sum(1 for ub in ub_res.data if ub.get("rating") == "bad")
    has_feedback = any(ub.get("feedback_embedding") for ub in ub_res.data)

    # 메모리 가드 슬롯이 있으면 즉시(inline) 계산해 첫 호출에 개인화 추천을 바로 준다.
    liked_books = {ub["book_id"]: {"rating": ub.get("rating", "neutral")} for ub in ub_res.data}
    fb_data = {}
    for ub in ub_res.data:
        emb = ub.get("feedback_embedding")
        if emb:
            fb_data[ub["book_id"]] = {"emb": to_np(emb), "is_dislike": ub.get("rating") == "bad"}

    # C4 트리거 술어: 좋/싫 책 중 인덱스 밖이 있거나(임베딩 필요) 태그/리뷰 있는데
    # feedback_embedding 없는 행이 있으면 백그라운드 recompute 가 임베딩+보강해야 한다.
    bid_set = set(request.app.state.bid_order or [])
    needs_bg = any(
        (ub.get("rating") in ("good", "bad") and ub["book_id"] not in bid_set)
        or ((ub.get("emotion_tags") or ub.get("review_text")) and not ub.get("feedback_embedding"))
        for ub in ub_res.data
    )
    # 이미 임베딩된 인덱스 밖 책은 inline 에서도 즉시 반영(OpenAI 없이 DB read).
    extra_query = resolve_extra_query_vectors(list(liked_books.keys()), bid_set, sb) if bid_set else {}

    scored = await try_compute_inline(request.app.state, liked_books, fb_data, extra_query=extra_query)
    if scored is not None:
        books_meta = request.app.state.books_meta
        recs, recs_for_cache = [], []
        for bid, score in scored:
            meta = books_meta.get(bid)
            if meta is None:
                continue
            book = BookScore(book_id=bid, score=round(score, 4),
                             title=meta.get("title", ""), author=meta.get("author", ""),
                             cover_url=meta.get("cover_url"))
            recs.append(book)
            recs_for_cache.append(book.dict())
        if needs_bg:
            # 미임베딩 책/피드백 → 빈약 캐시 저장 skip(코히런스), 백그라운드가 임베딩+보강 후 확정.
            background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
        else:
            background_tasks.add_task(save_cache_if_current, user_id, recs_for_cache,
                                      input_hash, total_liked, total_disliked, has_feedback)
        recs = dedup_by_work(recs, lambda b: (b.title, b.author))
        return RecommendResponse(
            user_id=user_id, recommendations=recs[:limit],
            meta={"total_liked": total_liked, "total_disliked": total_disliked,
                  "has_feedback": has_feedback},
        )

    # 슬롯 없음(동시 다발) → 메모리 보호: 백그라운드 재계산 + fallback(stale/computing).
    background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
    if cache and cache.get("recommendations"):
        recs = _dedup_cached(cache["recommendations"], limit)
        return RecommendResponse(
            user_id=user_id, recommendations=recs,
            meta={"total_liked": total_liked, "total_disliked": total_disliked,
                  "has_feedback": has_feedback,
                  "from_cache": True, "stale": True, "computing": True},
        )
    return RecommendResponse(
        user_id=user_id, recommendations=[],
        meta={"total_liked": total_liked, "total_disliked": total_disliked,
              "has_feedback": has_feedback, "computing": True},
    )
