"""
engine/cache.py — recommendation_cache 테이블 관련 캐시 유틸리티

주요 함수:
  compute_input_hash   — user_books 상태의 SHA256 해시 (정렬 기반, 순서 무관)
  load_cache           — recommendation_cache SELECT
  save_cache_if_current — input_hash 일치 시에만 UPSERT (stale write 방지)
  recompute_recommendations — BackgroundTask용 전체 재계산
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from config import CACHE_TOP_N, get_supabase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

def compute_input_hash(user_books_data: list[dict]) -> str:
    """
    user_books_data의 현재 상태를 나타내는 SHA256 해시를 반환한다.

    각 행을 "{book_id}:{rating}:{has_fb}" 문자열로 변환하고
    정렬한 뒤 연결하여 해싱한다 — 행 순서에 무관하다.

    Args:
        user_books_data: user_books 테이블 SELECT 결과 (list[dict])
                         각 dict는 book_id, rating, feedback_embedding 키를 포함한다.

    Returns:
        64자 소문자 16진수 문자열 (SHA256)
    """
    entries: list[str] = []
    for row in user_books_data:
        book_id = str(row.get("book_id", ""))
        rating = str(row.get("rating", "neutral"))
        has_fb = "1" if row.get("feedback_embedding") else "0"
        entries.append(f"{book_id}:{rating}:{has_fb}")

    entries.sort()
    raw = "|".join(entries).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# load_cache
# ---------------------------------------------------------------------------

def load_cache(user_id: str) -> Optional[dict]:
    """
    recommendation_cache 테이블에서 user_id에 해당하는 행을 반환한다.
    없으면 None을 반환한다.
    """
    try:
        sb = get_supabase()
        res = sb.table("recommendation_cache").select("*").eq("user_id", user_id).execute()
        if res.data:
            return res.data[0]
    except Exception as exc:
        logger.warning("load_cache failed for user %s: %s", user_id, exc)
    return None


# ---------------------------------------------------------------------------
# save_cache_if_current
# ---------------------------------------------------------------------------

def save_cache_if_current(
    user_id: str,
    recommendations: list[dict],
    input_hash: str,
    good_count: int,
    bad_count: int,
    has_feedback: bool,
) -> None:
    """
    현재 input_hash가 DB의 최신 상태와 일치할 때만 UPSERT한다.

    중간에 피드백이 추가됐을 경우(input_hash 불일치) 저장하지 않아
    stale write를 방지한다.

    Args:
        user_id:         Supabase auth.users UUID (문자열)
        recommendations: 저장할 추천 목록 (최대 CACHE_TOP_N개, dict 형태)
        input_hash:      계산 시점의 input_hash
        good_count:      좋아요 수
        bad_count:       싫어요 수
        has_feedback:    피드백 임베딩 보유 여부
    """
    # 현재 DB의 input_hash 재확인 (computing 중 피드백 발생 방지)
    current = load_cache(user_id)
    if current and current.get("computing") and current.get("input_hash") != input_hash:
        logger.info(
            "save_cache_if_current: hash mismatch for user %s — skipping stale write", user_id
        )
        return

    row = {
        "user_id": user_id,
        "recommendations": recommendations[:CACHE_TOP_N],
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "good_count": good_count,
        "bad_count": bad_count,
        "has_feedback": has_feedback,
        "input_hash": input_hash,
        "computing": False,
    }
    try:
        sb = get_supabase()
        sb.table("recommendation_cache").upsert(row, on_conflict="user_id").execute()
    except Exception as exc:
        logger.warning("save_cache_if_current failed for user %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# recompute_recommendations (BackgroundTask)
# ---------------------------------------------------------------------------

def recompute_recommendations(user_id: str, app_state) -> None:
    """
    피드백 저장 후 백그라운드에서 추천을 재계산하고 캐시를 갱신한다.

    app_state는 FastAPI Application의 state 객체여야 하며,
    아래 속성을 포함해야 한다:
      - index, books_meta, prestacked_reasons
      - desc_matrix_f16, agg_reason_matrix_f16, bid_order
      - built_at (optional)

    computing 플래그를 true로 먼저 설정하여 중복 재계산을 억제한다.
    """
    from config import STAGE1_TOP_N
    from engine.twostage import stage1_hybrid, batch_score_prestacked
    from engine.scorer import recommend_scores
    from engine.utils import to_np

    sb = get_supabase()

    # computing 플래그 설정
    try:
        sb.table("recommendation_cache").upsert(
            {"user_id": user_id, "computing": True,
             "input_hash": "__computing__",
             "recommendations": [], "computed_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="user_id",
        ).execute()
    except Exception as exc:
        logger.warning("recompute: failed to set computing flag for %s: %s", user_id, exc)

    try:
        ub_res = sb.table("user_books").select(
            "book_id,rating,feedback_embedding"
        ).eq("user_id", user_id).execute()

        if not ub_res.data:
            # 유저 데이터 없음 — 캐시 초기화
            sb.table("recommendation_cache").upsert(
                {"user_id": user_id, "recommendations": [], "computing": False,
                 "input_hash": compute_input_hash([]),
                 "computed_at": datetime.now(timezone.utc).isoformat(),
                 "good_count": 0, "bad_count": 0, "has_feedback": False},
                on_conflict="user_id",
            ).execute()
            return

        input_hash = compute_input_hash(ub_res.data)

        liked_books: dict = {}
        fb_data: dict = {}
        good_count = bad_count = 0
        has_feedback = False

        for ub in ub_res.data:
            bid = ub["book_id"]
            rating = ub.get("rating", "neutral")
            liked_books[bid] = {"rating": rating}
            if rating == "good":
                good_count += 1
            elif rating == "bad":
                bad_count += 1
            fb_emb = ub.get("feedback_embedding")
            if fb_emb:
                has_feedback = True
                fb_data[bid] = {"emb": to_np(fb_emb), "is_dislike": rating == "bad"}

        prestacked = app_state.prestacked_reasons
        if prestacked is not None:
            candidates = stage1_hybrid(
                liked_books, fb_data,
                app_state.desc_matrix_f16,
                app_state.agg_reason_matrix_f16,
                app_state.bid_order,
                top_n=STAGE1_TOP_N,
            )
            scores = batch_score_prestacked(
                app_state.index, liked_books, fb_data, candidates, prestacked)
        else:
            scores = recommend_scores(app_state.index, liked_books, fb_data)

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:CACHE_TOP_N]

        recs: list[dict] = []
        for bid, score in sorted_scores:
            meta = app_state.books_meta.get(bid)
            if meta is None:
                continue
            recs.append({
                "book_id": bid,
                "score": round(score, 4),
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "cover_url": meta.get("cover_url"),
            })

        save_cache_if_current(user_id, recs, input_hash, good_count, bad_count, has_feedback)

    except Exception as exc:
        logger.error("recompute_recommendations failed for user %s: %s", user_id, exc)
        # computing 플래그 해제
        try:
            sb.table("recommendation_cache").update({"computing": False}).eq(
                "user_id", user_id
            ).execute()
        except Exception:
            pass
