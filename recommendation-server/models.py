from typing import Optional, List
from pydantic import BaseModel


class BookScore(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: Optional[str]


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
    rating: str  # "good" | "neutral" | "bad"
    review_text: Optional[str] = None
    emotion_tags: Optional[List[str]] = None


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: Optional[str] = None
