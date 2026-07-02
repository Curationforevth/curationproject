from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from auth import verify_jwt
from models import RecommendResponse, BookScore
from engine.cache import (compute_input_hash, load_cache,
                          recompute_recommendations)
from engine.dedup import dedup_by_work
from engine.reasons import attach_reasons
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
            recs = attach_reasons(sb, _dedup_cached(cache["recommendations"], limit))
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
            recs = attach_reasons(sb, _dedup_cached(cache["recommendations"], limit))
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

    # 인라인(동기) 스코어링 금지 — 무료 단일 CPU 에서 8~17s 라 요청을 막으면 UX 파탄
    # (NN/g: 1s 면 주의 이탈). 좋아요 변경 시 /recompute 가 선제 백그라운드 재계산을 걸어
    # 이 시점엔 대개 캐시 히트다. 미스여도 여기서 계산하지 않고 백그라운드 재계산 트리거 +
    # 즉시 반환한다(이전 recs 있으면 그것 + computing, 없으면 빈 결과 + computing).
    # 앱은 computing 이면 skeleton + 진행표시를 보여주고 다음 호출에서 warm 캐시를 받는다.
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
