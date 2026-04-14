from __future__ import annotations

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from auth import verify_jwt
from models import FeedbackRequest, FeedbackResponse
from config import (get_supabase,
                    OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
from engine.cache import recompute_recommendations

router = APIRouter()


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


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_jwt),
):
    sb = get_supabase()
    fb_embedding = None

    if req.review_text and req.review_text.strip():
        try:
            fb_embedding = _embed_text(req.review_text.strip())
        except Exception:
            pass  # save text anyway, embedding retried in batch

    row = {
        "user_id": current_user,
        "book_id": str(req.book_id),
        "rating": req.rating,
        "review_text": req.review_text,
        "emotion_tags": req.emotion_tags,
    }
    if fb_embedding:
        row["feedback_embedding"] = fb_embedding

    try:
        sb.table("user_books").upsert(row, on_conflict="user_id,book_id").execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")

    # 피드백 저장 후 백그라운드에서 추천 재계산
    if request.app.state.prestacked_reasons is not None:
        background_tasks.add_task(recompute_recommendations, current_user, request.app.state)

    return FeedbackResponse(status="ok")
