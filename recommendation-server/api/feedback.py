import uuid
import requests
from fastapi import APIRouter, Depends, HTTPException
from supabase import create_client
from auth import verify_jwt
from models import FeedbackRequest, FeedbackResponse
from config import (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                    OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)

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
    current_user: str = Depends(verify_jwt),
):
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    feedback_id = str(uuid.uuid4())
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

    return FeedbackResponse(status="ok", feedback_id=feedback_id)
