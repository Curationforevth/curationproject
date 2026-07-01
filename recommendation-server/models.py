from __future__ import annotations

from typing import Literal, Optional, List
from pydantic import BaseModel


class BookScore(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: Optional[str]
    # 취향 발견 surfacing: 이 책의 대표 "좋아할 이유"(book_love_reasons). 없으면 None.
    reason: Optional[str] = None


class RecommendResponse(BaseModel):
    user_id: str
    recommendations: List[BookScore]
    meta: dict


class SimilarBook(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: Optional[str]


class SimilarResponse(BaseModel):
    book_id: str
    similar: List[SimilarBook]


class FeedbackRequest(BaseModel):
    book_id: str
    rating: Literal["good", "neutral", "bad"]
    review_text: Optional[str] = None
    emotion_tags: Optional[List[str]] = None


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: Optional[str] = None


class SimilarUnionRequest(BaseModel):
    book_ids: List[str]
    limit: int = 6
