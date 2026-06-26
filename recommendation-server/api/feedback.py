from __future__ import annotations

import logging

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from auth import verify_jwt
from models import FeedbackRequest, FeedbackResponse
from config import (get_supabase,
                    OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
from engine.cache import recompute_recommendations

router = APIRouter()
logger = logging.getLogger(__name__)


def _embed_text(text: str) -> list[float]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": [text],
              "dimensions": EMBEDDING_DIMENSIONS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def _embed_and_recompute(user_id: str, book_id: str, review_text, app_state) -> None:
    """BackgroundTask: 리뷰 임베딩(있으면) → user_books 갱신 → 추천 재계산.

    요청경로에서 OpenAI 동기 호출을 제거해 /feedback 을 비차단으로 만든다
    (과거: review_text 임베딩이 요청을 최대 30s 블로킹). 임베딩 → user_books 갱신 →
    recompute 순서를 보장해 리뷰 신호가 곧바로 추천에 반영된다([[feedback_accumulate]]
    "DB 축적하고 즉시 활용"). 임베딩 실패 시 feedback_embedding 은 null 로 남고
    scripts/backfill_feedback_embedding.py 가 다음 run 에 채운다(신호 소실 0).
    """
    sb = get_supabase()
    if review_text and review_text.strip():
        try:
            emb = _embed_text(review_text.strip())
            sb.table("user_books").update({"feedback_embedding": emb}).eq(
                "user_id", user_id).eq("book_id", book_id).execute()
        except Exception as e:
            # 실패해도 크래시하지 않는다 — null 로 두면 backfill 배치가 채운다.
            logger.warning("feedback embed failed (backfill 이 채움) u=%s b=%s: %s",
                           user_id, book_id, e)
    if app_state.index is not None:
        recompute_recommendations(user_id, app_state)


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_jwt),
):
    sb = get_supabase()

    row = {
        "user_id": current_user,
        "book_id": str(req.book_id),
        "rating": req.rating,
        "review_text": req.review_text,
        "emotion_tags": req.emotion_tags,
        # status 미지정 시 신규행이 기본값 'wishlist' 로 들어가는데, rating 이 있으면
        # user_books_wishlist_no_rating 제약(status<>'wishlist' OR rating IS NULL) 위반
        # → 500. 평가=읽음으로 보고 'finished' 로 둔다.
        "status": "finished",
    }
    # feedback_embedding 은 요청경로에서 넣지 않는다 — 임베딩(OpenAI)은 백그라운드로.
    # on_conflict 업데이트는 payload 에 없는 컬럼을 건드리지 않으므로 기존 임베딩은 보존,
    # 백그라운드 태스크가 새 review_text 로 갱신한다.
    try:
        sb.table("user_books").upsert(row, on_conflict="user_id,book_id").execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")

    # 임베딩(OpenAI) + 추천 재계산을 모두 백그라운드로 — 요청경로에서 외부 API 호출 제거.
    # _embed_and_recompute 가 임베딩→user_books 갱신→recompute 순서를 보장한다.
    # (recompute 는 prestacked=None(v3)이면 recommend_scores 로 폴백하므로 인덱스 로드
    # 여부로만 게이트되며, 이 게이트는 _embed_and_recompute 내부에 있다.)
    background_tasks.add_task(
        _embed_and_recompute, current_user, str(req.book_id),
        req.review_text, request.app.state)

    return FeedbackResponse(status="ok")
