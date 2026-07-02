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
import time
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


def rec_cache_reusable(cache: Optional[dict], ub_hash: str, built_at: str) -> bool:
    """recommendation_cache 를 재사용(재계산 없이 서빙)해도 되는지 판정.

    True 조건: computing 중 아님 + 추천 있음 + input_hash 가 현재 user_books 와 일치 +
    인덱스 빌드 이후 계산됨(computed_at > built_at). 마지막 조건이 핵심 — 이게 없으면
    인덱스 재빌드 후에도 옛 인덱스로 계산된 추천을 계속 서빙한다(/recommend 와 동일 기준).
    """
    if not cache:
        return False
    return (not cache.get("computing")
            and bool(cache.get("recommendations"))
            and cache.get("input_hash") == ub_hash
            and cache.get("computed_at", "") > (built_at or ""))


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

def compute_input_hash(user_books_data: list[dict],
                       signals: list[dict] | None = None) -> str:
    """
    추천 계산 입력 상태(user_books + 행동 신호)의 SHA256 해시를 반환한다.

    각 user_books 행을 "{book_id}:{rating}:{has_fb}:{status}", 각 신호 행을
    "s:{book_id}:{signal}" 문자열로 변환하고 정렬한 뒤 연결하여 해싱한다 —
    행 순서에 무관하다.

    status 포함 이유(2026-07-02): wishlist 가 약한 긍정 신호로 스코어링에
    들어가면서 상태 전이(wishlist→finished 등)가 계산 입력을 바꾸게 됨.
    signals(user_book_signals 행): 관심없음이 후보 제외+음수 항으로 계산에
    들어가므로 해시에도 포함 — 신호 변경 시 캐시가 자동 무효화된다.

    ⚠️ 엔트리 포맷 변경은 배포 시 전 유저 캐시 1회 무효화를 유발한다
    (다음 요청에서 백그라운드 재계산, warm ~5.7s) — 의도된 비용.

    Returns:
        64자 소문자 16진수 문자열 (SHA256)
    """
    entries: list[str] = []
    for row in user_books_data:
        book_id = str(row.get("book_id", ""))
        rating = str(row.get("rating", "neutral"))
        has_fb = "1" if row.get("feedback_embedding") else "0"
        status = str(row.get("status", ""))
        entries.append(f"{book_id}:{rating}:{has_fb}:{status}")
    for row in signals or []:
        entries.append(f"s:{row.get('book_id', '')}:{row.get('signal', '')}")

    entries.sort()
    raw = "|".join(entries).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_signals(sb, user_id: str) -> list[dict]:
    """user_book_signals 행(book_id, signal)을 읽는다 — 유저당 수십 행 수준.

    서빙(/recommend·/home)과 재계산이 공유: 해시 계산과 관심없음 필터/제외가
    같은 데이터를 봐야 한다(코히런스). 실패 처리는 호출측(_safe/try) 책임.
    """
    res = sb.table("user_book_signals").select(
        "book_id,signal").eq("user_id", user_id).execute()
    return res.data or []


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
        sb_live = get_supabase()
        live = sb_live.table("user_books").select(
            "book_id,rating,feedback_embedding,status"
        ).eq("user_id", user_id).execute()
        live_hash = compute_input_hash(live.data or [], load_signals(sb_live, user_id))
        if live_hash != input_hash:
            logger.info(
                "save_cache_if_current: live state moved past computed input "
                "for user %s — skipping stale write", user_id
            )
            # computing 을 반드시 내린다 — 계산 중 유저가 좋아요를 더 바꾸면 그 변경의
            # /recompute 트리거는 computing=true 에 걸려 skip 됐다. 여기서 안 내리면
            # 다음 트리거까지 캐시가 STUCK 가드(180s)에 갇히는 잠재 데드락(저장은
            # 어차피 skip 이므로 플래그만 해제 → 다음 트리거/캐시미스가 즉시 재계산).
            try:
                get_supabase().table("recommendation_cache").update(
                    {"computing": False}).eq("user_id", user_id).execute()
            except Exception:
                pass
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

    # 스테이지별 소요시간(초). prod 재계산 8~17s 의 분해(I/O vs stage1 vs stage2)를
    # 관측하기 위한 계측 — Phase 2 계산 단축의 전/후 비교 기준. 로그 한 줄 +
    # app_state.last_recompute_timings(/health 노출)로 남긴다. 행동/점수 무변경.
    t_start = time.perf_counter()
    timings: dict = {}
    _t = [t_start]

    def _mark(key: str) -> None:
        now = time.perf_counter()
        timings[key] = round(now - _t[0], 3)
        _t[0] = now

    # 이미 computing 중이면 skip — 단 stuck(중단돼 오래 켜진) 플래그는 예외로 재시도한다.
    # (서버 재시작/OOM 로 재계산이 죽으면 computing 이 영구 true 로 남아 이후 모든
    #  재계산이 skip → 캐시가 영영 안 풀리는 데드락. computed_at 나이로 stuck 판정.)
    existing = load_cache(user_id)
    _mark("load_cache")
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
    # 기존 행은 UPDATE 로 플래그 컬럼만 갱신(recs 를 되보내는 upsert 는 왕복 payload 만
    # 키움 — 미접촉이 곧 보존). 행이 없을 때만 최소 행 upsert.
    try:
        flag_cols = {"computing": True, "input_hash": "__computing__",
                     "computed_at": datetime.now(timezone.utc).isoformat()}
        if existing:
            sb.table("recommendation_cache").update(flag_cols).eq(
                "user_id", user_id).execute()
        else:
            sb.table("recommendation_cache").upsert(
                {"user_id": user_id, "recommendations": [], **flag_cols},
                on_conflict="user_id",
            ).execute()
    except Exception as exc:
        logger.warning("recompute: failed to set computing flag for %s: %s", user_id, exc)
    _mark("flag")

    if app_state.prestacked_reasons is None:
        # v3 폴백은 augment 미적용 — prod 는 v4-prestacked 라 도달 안 함. 회귀 가시화.
        logger.warning("recompute: prestacked is None — v3 fallback (extra_query 미적용) u=%s", user_id)

    try:
        # 1차 read: 임베딩 판정용 emotion_tags/review_text + 신호용 status 포함
        ub_res = sb.table("user_books").select(
            "book_id,rating,feedback_embedding,emotion_tags,review_text,status"
        ).eq("user_id", user_id).execute()
        _mark("db1")

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
        # C1: 인덱스 밖 rated 책만 — 인덱스 내 책은 빌드가 book_v3_vectors 에서 만들어
        # 존재가 보장되므로 조회 자체를 skip(평상시 SELECT 1회 절약, 전부 인덱스 내면 0콜).
        bid_order_set = set(app_state.bid_order or [])
        ensure_books_embedded(
            [r["book_id"] for r in rated if r["book_id"] not in bid_order_set], sb)
        _mark("embed")

        # 행동 신호(관심없음) — 후보 제외 + 약한 음수 항 + 해시 포함. 실패는 빈
        # 목록으로 강등(추천이 신호 없이라도 계산되는 게 낫다).
        try:
            signals = load_signals(sb, user_id)
        except Exception as exc:
            logger.warning("recompute: load_signals failed for %s: %s", user_id, exc)
            signals = []
        not_interested_ids = {s["book_id"] for s in signals
                              if s.get("signal") == "not_interested"}

        # 재read(구 db2) 제거 — ensure_feedback_embedded 가 성공분을 행에 in-place
        # 반영하므로 이 시점의 ub_res.data 가 곧 스코어링 입력. 그 상태를 그대로
        # 해싱해야 hash 와 스코어 입력이 정확히 일치(코히런스). 계산 중 유저 변경의
        # staleness 는 save_cache_if_current 의 live 체크가 가드.
        input_hash = compute_input_hash(ub_res.data, signals)

        liked_books: dict = {}
        fb_data: dict = {}
        wishlist_ids: list = []
        good_count = bad_count = 0
        has_feedback = False

        for ub in ub_res.data:
            bid = ub["book_id"]
            rating = ub.get("rating", "neutral")
            liked_books[bid] = {"rating": rating}
            if ub.get("status") == "wishlist":
                wishlist_ids.append(bid)  # 읽고싶어요 = 약한 긍정 신호(config 주석)
            if rating == "good":
                good_count += 1
            elif rating == "bad":
                bad_count += 1
            fb_emb = ub.get("feedback_embedding")
            if fb_emb:
                has_feedback = True
                fb_data[bid] = {"emb": to_np(fb_emb), "is_dislike": rating == "bad"}

        # C2: 정적 인덱스 밖 좋/싫 책 벡터를 DB 에서 읽어 쿼리로 주입 (OpenAI 없음).
        extra_query = resolve_extra_query_vectors(
            list(liked_books.keys()), bid_order_set, sb)
        _mark("extra")

        prestacked = app_state.prestacked_reasons
        if prestacked is not None:
            candidates = stage1_hybrid(
                liked_books, fb_data,
                app_state.desc_matrix_f16,
                app_state.agg_reason_matrix_f16,
                app_state.bid_order,
                top_n=STAGE1_TOP_N,
                extra_query=extra_query,
                wishlist_ids=wishlist_ids,
                not_interested_ids=not_interested_ids,
            )
            _mark("s1")
            scores = batch_score_prestacked(
                app_state.index, liked_books, fb_data, candidates, prestacked,
                extra_query=extra_query,
                wishlist_ids=wishlist_ids,
                not_interested_ids=not_interested_ids)
            _mark("s2")
        else:
            scores = recommend_scores_two_stage(
                app_state.index, liked_books, fb_data, top_n=STAGE1_TOP_N)
            _mark("s2")

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
        _mark("save")

        timings["total"] = round(time.perf_counter() - t_start, 3)
        timings["n_books"] = len(liked_books)
        logger.info(
            "recompute timings u=%s %s", user_id,
            " ".join(f"{k}={v}" for k, v in timings.items()),
        )
        try:
            app_state.last_recompute_timings = timings
        except Exception:
            pass

    except Exception as exc:
        logger.error("recompute_recommendations failed for user %s: %s", user_id, exc)
        # computing 플래그 해제
        try:
            sb.table("recommendation_cache").update({"computing": False}).eq(
                "user_id", user_id
            ).execute()
        except Exception:
            pass
