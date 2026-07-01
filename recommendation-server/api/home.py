"""recommendation-server/api/home.py

/home/{user_id} — User Tier 분기 후 섹션 조립. Spec §6.2.

쿼리 수: user_state 1 + user_books 1 + active themes 1 + curation_cache IN-clause 1
+ recommendation_stage 1 (+ fallback_curation 1)
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from auth import verify_jwt
from config import get_supabase
from engine.tier import (
    user_tier_from_likes, cta_for_tier, sections_for_tier, similar_section_title,
)
from engine.curation import (
    filter_by_personalization, apply_recent_discount, weighted_sample_one,
    RECENT_CURATION_WINDOW_DAYS,
)
from engine.home_cache import (
    current_hour_bucket, compute_home_input_hash,
    load_home_cache, save_home_cache_if_current,
)
from engine.cache import load_cache, compute_input_hash, recompute_recommendations
from engine.recommend_core import try_compute_inline
from engine.user_embed import resolve_extra_query_vectors, needs_background_embed
from engine.dedup import dedup_by_work, dedup_similar
from engine.utils import to_np

import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# 책 목록이 아니라 네비게이션 요소라 비어도 유지하는 섹션 타입.
_NON_BOOK_SECTIONS = {"category_nav"}


def _safe(fn, *, default):
    """Supabase 쿼리 하나가 실패해도 /home 전체가 500 으로 죽지 않도록 감싼다.
    앱이 /home 을 직접 그리므로(큐레이션/트렌딩 노출) 한 쿼리 장애가 화면을
    통째로 깨면 안 된다. 실패 시 default 로 degrade(해당 섹션만 비고 나머지는 정상)."""
    try:
        return fn()
    except Exception as e:
        logger.warning("[home] query failed, degrading: %s", e)
        return default


def _drop_empty_sections(sections: list[dict]) -> list[dict]:
    """책이 없는 빈 섹션을 제거 — 앱에 제목만 있고 책 없는 빈 줄이 내려가지 않게 한다.
    (category_nav 는 네비게이션 요소라 비어도 유지.) tier2 에서 tier2+ 큐레이션 테마가
    없을 때 빈 큐레이션 섹션이 내려가던 실측 버그 방지."""
    return [s for s in sections
            if s.get("books") or s.get("type") in _NON_BOOK_SECTIONS]


def _book_dict(bid: str, books_meta: dict, score: Optional[float] = None) -> Optional[dict]:
    meta = books_meta.get(bid)
    if meta is None:
        return None  # skip ghost book
    d = {
        "book_id": bid,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "cover_url": meta.get("cover_url"),
    }
    if score is not None:
        d["score"] = round(score, 4)
    return d


def _similar_books_from_seed(index, books_meta: dict, seed_book_id: str, limit: int = 10) -> list[dict]:
    try:
        # 시그니처는 similar_by_desc(book_id, limit=10). 과거 top_n= 오인자로 TypeError
        # 가 나고 except 가 삼켜 Tier 1(personal_recommend 없음) 유저의 similar 섹션이
        # 통째로 비어있던 버그. except 도 더는 무음으로 삼키지 않는다.
        # over-fetch 후 시드의 다른 판본·중복 판본 제거 → limit 개.
        raw = index.similar_by_desc(seed_book_id, limit=limit * 2 + 5)
        results = dedup_similar(raw, books_meta, seed_book_id, limit)
    except Exception as e:
        print(f"[home] similar_by_desc failed (seed={seed_book_id}): {e}", flush=True)
        return []
    out = []
    for bid, _score in results:
        b = _book_dict(bid, books_meta)
        if b:
            out.append(b)
    return out


def assemble_sections_for_user(
    *,
    tier: int,
    stage: int,
    total_likes: int,
    user_books: list[dict],
    top_authors: list[str],
    top_l1s: list[str],
    recent_curation_ids: set,
    fallback_books: list[dict],
    active_themes: list[dict],
    curation_cache_by_id: dict,
    books_meta: dict,
    index,
    recommend_scored: Optional[list] = None,
) -> list[dict]:
    """Tier별 섹션 구성 규칙에 따라 실제 books 리스트를 조립한다."""
    templates = sections_for_tier(tier)
    sections: list[dict] = []

    # 최근 좋아한 책 id (similar seed)
    latest_liked_bid = None
    for ub in user_books:
        if ub.get("rating") == "good":
            latest_liked_bid = ub["book_id"]
            break  # user_books 는 updated_at DESC 정렬 가정

    for idx, tpl in enumerate(templates):
        stype = tpl["type"]
        section_id = f"{stype}_{idx}"

        if stype == "personal_recommend":
            books = []
            # 같은 작품의 다른 판본이 "당신을 위한 추천"에 중복으로 뜨지 않게 접는다.
            scored = dedup_by_work(
                list(recommend_scored or []),
                lambda t: (books_meta.get(t[0], {}).get("title", ""),
                           books_meta.get(t[0], {}).get("author", "")),
            )
            for bid, score in scored[:10]:
                b = _book_dict(bid, books_meta, score=score)
                if b:
                    books.append(b)
            sections.append({
                "id": section_id, "type": "personal_recommend",
                "title": "당신을 위한 추천", "books": books,
                "algorithm_version": "h10_stage0",
            })

        elif stype == "similar":
            if latest_liked_bid and latest_liked_bid in books_meta:
                seed_title = books_meta[latest_liked_bid].get("title", "")
                books = _similar_books_from_seed(index, books_meta, latest_liked_bid)
                sections.append({
                    "id": section_id, "type": "similar",
                    "title": similar_section_title(seed_title),
                    "seed_book_id": latest_liked_bid,
                    "books": books,
                })
            else:
                # fallback: general curation
                sections.append(_sample_curation(
                    active_themes, top_authors, top_l1s, tier, recent_curation_ids,
                    curation_cache_by_id, books_meta, personalization_override="general",
                    section_id=section_id,
                ))

        elif stype == "curation":
            sections.append(_sample_curation(
                active_themes, top_authors, top_l1s, tier, recent_curation_ids,
                curation_cache_by_id, books_meta,
                personalization_override=tpl.get("personalization"),
                section_id=section_id,
            ))

        elif stype == "trending":
            books = []
            for row in fallback_books[:10]:
                b = _book_dict(row["book_id"], books_meta)
                if b:
                    books.append(b)
            sections.append({
                "id": section_id, "type": "trending",
                "title": "화제의 책", "books": books,
            })

        elif stype == "category_nav":
            sections.append({
                "id": section_id, "type": "category_nav", "books": [],
            })

    return sections


def _sample_curation(
    active_themes, top_authors, top_l1s, tier, recent_ids,
    cache_by_id, books_meta, *, personalization_override=None, section_id,
) -> dict:
    # 개인화 필터
    pool = [t for t in active_themes
            if personalization_override is None
            or t.get("personalization") == personalization_override]
    pool = filter_by_personalization(pool, tier=tier,
                                     top_authors=top_authors, top_l1s=top_l1s)
    pool = apply_recent_discount(pool, recent_ids)

    # by_author/by_l1 fallback → general
    if not pool and personalization_override in ("by_author", "by_l1"):
        pool = [t for t in active_themes if t.get("personalization") == "general"]
        pool = apply_recent_discount(pool, recent_ids)

    picked = weighted_sample_one(pool)
    if picked is None:
        return {"id": section_id, "type": "curation", "books": []}

    book_ids = cache_by_id.get(picked["id"], [])
    books: list[dict] = []
    for bid in book_ids[:10]:
        b = _book_dict(bid, books_meta)
        if b:
            books.append(b)

    return {
        "id": f"curation_{picked['id']}",
        "type": "curation",
        "title": picked.get("title", ""),
        "description": picked.get("description"),
        "curation_id": picked["id"],
        "personalization": picked.get("personalization"),
        "books": books,
    }


@router.get("/home/{user_id}")
async def get_home(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    refresh: bool = Query(False),
    current_user: str = Depends(verify_jwt),
):
    if current_user != user_id:
        raise HTTPException(403, "Cannot access other user's home")

    sb = get_supabase()

    us_data = _safe(lambda: sb.table("user_state").select(
        "current_tier,total_likes,top_authors,top_l1s,updated_at"
    ).eq("user_id", user_id).limit(1).execute().data, default=[])

    us = (us_data[0] if us_data else None) or {
        "current_tier": 0, "total_likes": 0,
        "top_authors": [], "top_l1s": [], "updated_at": "",
    }
    tier = us["current_tier"]
    total_likes = us["total_likes"]
    top_authors = [a["author"] for a in (us.get("top_authors") or [])]
    top_l1s = [l["l1"] for l in (us.get("top_l1s") or [])]

    # stage
    stage_data = _safe(lambda: sb.table("recommendation_stage").select(
        "current_stage").eq("id", 1).limit(1).execute().data, default=[])
    stage = (stage_data[0]["current_stage"] if stage_data else 0)

    # home_section_cache 확인
    hour_bucket = current_hour_bucket()
    input_hash = compute_home_input_hash(us.get("updated_at", ""), hour_bucket)
    cache = load_home_cache(user_id)

    # refresh=1 (당겨서 새로고침) 이면 hour-bucket 캐시 히트를 건너뛰고 섹션을 재조립한다.
    # → 큐레이션이 weighted_sample_one 으로 매번 새로 샘플링돼 "새 큐레이션"이 나온다.
    # 비싼 Tier2 personal_recommend 는 아래에서 recommendation_cache 를 그대로 재사용하므로
    # (요청경로 재스코어링 없음) force-refresh 여도 저렴하다. 재조립 결과는 background 로
    # home_cache 에 덮어써 이후 일반 로드가 같은 큐레이션을 일관되게 보게 한다.
    if not refresh and cache and cache.get("input_hash") == input_hash:
        # cache hit path도 impression + history 로깅 (스펙 §7.3 CTR 정확성)
        background_tasks.add_task(
            _log_impressions_and_history,
            user_id, cache["sections"], stage,
        )
        return {
            "user_id": user_id, "tier": tier, "stage": stage,
            "sections": cache["sections"],
            "cta": cta_for_tier(tier, total_likes),
            "computed_at": cache["computed_at"],
            "cache_hit": True,
        }

    # Miss → 섹션 조립용 데이터 조회
    user_books = _safe(lambda: sb.table("user_books").select(
        "book_id,rating,feedback_embedding,emotion_tags,review_text,updated_at"
    ).eq("user_id", user_id).order("updated_at", desc=True).execute().data,
        default=[]) or []

    active_themes = _safe(lambda: sb.table("curation_themes").select(
        "id,theme_type,title,description,personalization,"
        "target_l1,target_author,target_keyword,priority,click_rate,shown_count"
    ).eq("is_active", True).execute().data, default=[]) or []

    theme_ids = [t["id"] for t in active_themes]
    cache_rows = []
    if theme_ids:
        cache_rows = _safe(lambda: sb.table("curation_cache").select(
            "curation_id,book_ids,expires_at"
        ).in_("curation_id", theme_ids).execute().data, default=[]) or []

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    curation_cache_by_id = {
        r["curation_id"]: r["book_ids"]
        for r in cache_rows
        if r.get("expires_at", "") > now_iso
    }

    fallback_books = _safe(lambda: sb.table("fallback_curation").select(
        "book_id").order("rank").limit(30).execute().data, default=[]) or []

    seven_days_ago = (now - timedelta(days=RECENT_CURATION_WINDOW_DAYS)).isoformat()
    uch_data = _safe(lambda: sb.table("user_curation_history").select("curation_id").eq(
        "user_id", user_id
    ).gte("shown_at", seven_days_ago).execute().data, default=[])
    recent_curation_ids = {r["curation_id"] for r in (uch_data or [])}

    # Tier 2 — personal_recommend 는 미리 계산된 recommendation_cache 에서 가져온다.
    # 요청경로에서 스코어링하지 않는다(무료 단일 CPU ~70s). 캐시가 없거나 stale
    # (좋아요 변경) 이거나 계산 중이면 백그라운드 재계산을 트리거하고 이번 응답엔
    # personal_recommend 를 비운다(trending/curation 으로 fallback). 다음 로드에서 채워짐.
    recommend_scored = None
    recs_pending = False  # Tier2 추천이 아직 준비 안 됨(백그라운드 계산중)
    if tier == 2:
        rec_cache = load_cache(user_id)
        ub_hash = compute_input_hash(user_books)
        if (rec_cache and not rec_cache.get("computing")
                and rec_cache.get("recommendations")
                and rec_cache.get("input_hash") == ub_hash):
            recommend_scored = [(r["book_id"], r.get("score", 0.0))
                                for r in rec_cache["recommendations"]]
        else:
            # 캐시 없음/stale → 메모리 가드 슬롯 있으면 즉시 계산(첫 로드에 개인화).
            liked_books = {ub["book_id"]: {"rating": ub.get("rating", "neutral")} for ub in user_books}
            fb_data = {}
            for ub in user_books:
                emb = ub.get("feedback_embedding")
                if emb:
                    fb_data[ub["book_id"]] = {"emb": to_np(emb), "is_dislike": ub.get("rating") == "bad"}
            # C4 술어 + 인덱스 밖 책 즉시 반영(이미 임베딩된 것, OpenAI 없음).
            bid_set = set(getattr(request.app.state, "bid_order", None) or [])
            needs_bg = needs_background_embed(user_books, bid_set)
            extra_query = resolve_extra_query_vectors(
                [ub["book_id"] for ub in user_books], bid_set, sb) if bid_set else {}
            recommend_scored = await try_compute_inline(
                request.app.state, liked_books, fb_data, extra_query=extra_query)
            if recommend_scored is None:
                # 슬롯 없음(동시 다발) → 백그라운드 + 이번 응답엔 비움(다음 로드 반영).
                background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
                recommend_scored = []
                recs_pending = True
            elif needs_bg:
                # inline 성공했지만 미임베딩 책/피드백 있음 → 백그라운드 임베딩+보강.
                # 빈약본 캐시 방지(recs_pending=True 면 home_cache 미저장) → 다음 로드에 보강본.
                background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
                recs_pending = True

    sections = assemble_sections_for_user(
        tier=tier, stage=stage, total_likes=total_likes,
        user_books=user_books,
        top_authors=top_authors, top_l1s=top_l1s,
        recent_curation_ids=recent_curation_ids,
        fallback_books=fallback_books,
        active_themes=active_themes,
        curation_cache_by_id=curation_cache_by_id,
        books_meta=request.app.state.books_meta,
        index=request.app.state.index,
        recommend_scored=recommend_scored,
    )

    # 빈 책-섹션 제거 — 앱이 제목만 있고 책 없는 빈 줄을 그리지 않도록(category_nav 제외).
    sections = _drop_empty_sections(sections)

    # BackgroundTasks: cache write + impression INSERT + user_curation_history
    # Tier2 추천이 아직 준비 안 된(비어있는) 응답은 캐시하지 않는다 — 캐시하면 백그라운드
    # 재계산이 끝나도 같은 hour_bucket 동안 빈 personal_recommend 가 노출된다. 미저장 시
    # 다음 /home 이 재조립하여 준비된 추천을 즉시 반영한다.
    if not recs_pending:
        background_tasks.add_task(
            save_home_cache_if_current,
            user_id, sections, tier, stage, input_hash,
        )
    background_tasks.add_task(
        _log_impressions_and_history,
        user_id, sections, stage,
    )

    return {
        "user_id": user_id, "tier": tier, "stage": stage,
        "sections": sections,
        "cta": cta_for_tier(tier, total_likes),
        "computed_at": now_iso,
        "cache_hit": False,
    }


def _log_impressions_and_history(user_id: str, sections: list[dict], stage: int) -> None:
    """/home 섹션 노출 임프레션을 batch INSERT + user_curation_history 기록."""
    sb = get_supabase()
    imp_rows = []
    uch_rows = []
    SOURCE_MAP = {
        "personal_recommend": "home_recommend",
        "similar": "similar",
        "curation": "curation",
        "trending": "home_recommend",
    }
    for sec in sections:
        source = SOURCE_MAP.get(sec["type"], "home_recommend")
        curation_id = sec.get("curation_id")
        for pos, book in enumerate(sec.get("books", [])):
            imp_rows.append({
                "user_id": user_id,
                "book_id": book["book_id"],
                "position": pos,
                "source": source,
                "algorithm_version": f"h10_stage{stage}",
                "curation_id": curation_id,
            })
        if curation_id:
            uch_rows.append({"user_id": user_id, "curation_id": curation_id})

    try:
        if imp_rows:
            sb.table("recommendation_impressions").insert(imp_rows).execute()
        if uch_rows:
            sb.table("user_curation_history").insert(uch_rows).execute()
    except Exception as e:
        print(f"impression/history batch insert failed for {user_id}: {e}")
