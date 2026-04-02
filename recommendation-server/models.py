from pydantic import BaseModel


class BookScore(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: str | None


class RecommendResponse(BaseModel):
    user_id: str
    recommendations: list[BookScore]
    meta: dict


class SimilarBook(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: str | None


class SimilarResponse(BaseModel):
    book_id: str
    similar: list[SimilarBook]


class FeedbackRequest(BaseModel):
    book_id: str
    rating: str  # "good" | "neutral" | "bad"
    review_text: str | None = None
    emotion_tags: list[str] | None = None


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: str | None = None
