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

# computing 플래그가 이 시간(초)보다 오래 켜져 있으면 stuck(중단된 재계산)으로 보고
# 재계산을 재시도한다. 정상 재계산은 수~십수초라 넉넉한 마진. 이게 없으면 서버 재시작/
# OOM 으로 재계산이 중단됐을 때 computing 이 영구 true 로 고정돼 이후 모든 재계산이
# skip → 캐시가 영영 안 풀리는 데드락(실측: 한 유저가 이틀간 stuck → 매 /home 재계산).
STUCK_COMPUTING_SEC = 180


def _age_seconds(iso_ts: str) -> float:
    """ISO8601 타임스탬프의 경과 초. 파싱 실패 시 inf(=오래된 것으로 간주 → 재계산 허용)."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


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
    # stale-write 가드: *live user_books 상태* 가 우리가 계산한 input_hash 보다
    # 앞섰을 때만(= 계산 중 유저가 좋아요를 더 바꿨을 때) skip 한다.
    #
    # (과거: 캐시 *행* 의 hash 와 비교했다. 그러면 좋아요 burst(온보딩 6권) 중
    #  feedback 마다 걸리는 recompute 가 도중(예: 5권) 시점에 캐시를 확정하면,
    #  /recommend inline 이 6권으로 정확히 계산한 결과를 "hash mismatch" 로 거부 →
    #  캐시가 5권 hash 에 영구 고정 → 모든 /recommend 가 매번 ~8s 재계산하는 버그.
    #  비교 기준을 캐시 행이 아니라 live DB 로 바꿔 정답이 항상 저장되게 한다.)
    try:
        live = get_supabase().table("user_books").select(
            "book_id,rating,feedback_embedding"
        ).eq("user_id", user_id).execute()
        live_hash = compute_input_hash(live.data or [])
        if live_hash != input_hash:
            logger.info(
                "save_cache_if_current: live state moved past computed input "
                "for user %s — skipping stale write", user_id
            )
            return
    except Exception as exc:
        # live 확인 실패 시 보수적으로 저장 진행 — stale 캐시보다 최신 결과가 낫다.
        logger.warning("save_cache_if_current: live hash check failed for %s: %s", user_id, exc)

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
    from engine.scorer import recommend_scores_two_stage
    from engine.utils import to_np
    from engine.user_embed import (ensure_feedback_embedded, ensure_books_embedded,
                                   resolve_extra_query_vectors)

    sb = get_supabase()

    # 이미 computing 중이면 skip — 단 stuck(중단돼 오래 켜진) 플래그는 예외로 재시도한다.
    # (서버 재시작/OOM 로 재계산이 죽으면 computing 이 영구 true 로 남아 이후 모든
    #  재계산이 skip → 캐시가 영영 안 풀리는 데드락. computed_at 나이로 stuck 판정.)
    existing = load_cache(user_id)
    if existing and existing.get("computing"):
        age = _age_seconds(existing.get("computed_at", ""))
        if age < STUCK_COMPUTING_SEC:
            logger.info("recompute: already computing for %s — skipping", user_id)
            return
        logger.warning(
            "recompute: stale computing flag for %s (%.0fs old) — treating as stuck, retrying",
            user_id, age,
        )

    # computing 플래그 설정 — 기존 recommendations 는 보존(stale-serve 폴백 유지).
    # (blank 하면 inline 저장 skip 과 겹쳐 임베딩(수초) 동안 thin-only 가 됨.)
    try:
        sb.table("recommendation_cache").upsert(
            {"user_id": user_id, "computing": True,
             "input_hash": "__computing__",
             "recommendations": (existing or {}).get("recommendations", []),
             "computed_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="user_id",
        ).execute()
    except Exception as exc:
        logger.warning("recompute: failed to set computing flag for %s: %s", user_id, exc)

    if app_state.prestacked_reasons is None:
        # v3 폴백은 augment 미적용 — prod 는 v4-prestacked 라 도달 안 함. 회귀 가시화.
        logger.warning("recompute: prestacked is None — v3 fallback (extra_query 미적용) u=%s", user_id)

    try:
        # 1차 read: 임베딩 판정용으로 emotion_tags/review_text 포함
        ub_res = sb.table("user_books").select(
            "book_id,rating,feedback_embedding,emotion_tags,review_text"
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

        # C3+C1: 스코어링 전에 임베딩(요청경로 밖). per-book best-effort.
        rated = [r for r in ub_res.data if r.get("rating") in ("good", "bad")]
        for r in rated:
            r["user_id"] = user_id  # ensure_feedback_embedded 가 키로 사용
        ensure_feedback_embedded(rated, sb)                              # C3 (태그+리뷰)
        ensure_books_embedded([r["book_id"] for r in rated], sb)        # C1 (유저 책)

        # post-embedding 재read → input_hash 가 live(has_fb 반영)와 일치(코히런스).
        ub_res = sb.table("user_books").select(
            "book_id,rating,feedback_embedding"
        ).eq("user_id", user_id).execute()
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

        # C2: 정적 인덱스 밖 좋/싫 책 벡터를 DB 에서 읽어 쿼리로 주입 (OpenAI 없음).
        bid_order_set = set(app_state.bid_order or [])
        extra_query = resolve_extra_query_vectors(
            list(liked_books.keys()), bid_order_set, sb)

        prestacked = app_state.prestacked_reasons
        if prestacked is not None:
            candidates = stage1_hybrid(
                liked_books, fb_data,
                app_state.desc_matrix_f16,
                app_state.agg_reason_matrix_f16,
                app_state.bid_order,
                top_n=STAGE1_TOP_N,
                extra_query=extra_query,
            )
            scores = batch_score_prestacked(
                app_state.index, liked_books, fb_data, candidates, prestacked,
                extra_query=extra_query)
        else:
            scores = recommend_scores_two_stage(
                app_state.index, liked_books, fb_data, top_n=STAGE1_TOP_N)

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
