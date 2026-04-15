"""recommendation-server/api/home.py

/home/{user_id} — User Tier 분기 후 섹션 조립. Spec §6.2.

쿼리 수: user_state 1 + user_books 1 + active themes 1 + curation_cache IN-clause 1
+ recommendation_stage 1 (+ fallback_curation 1)
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth import verify_jwt
from config import get_supabase
from engine.tier import (
    user_tier_from_likes, cta_for_tier, sections_for_tier, similar_section_title,
)
from engine.curation import (
    filter_by_personalization, apply_recent_discount, weighted_sample_one,
)
from engine.home_cache import (
    current_hour_bucket, compute_home_input_hash,
    load_home_cache, save_home_cache_if_current,
)
from engine.recommend_core import compute_scored_books
from engine.utils import to_np

router = APIRouter()


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
        results = index.similar_by_desc(seed_book_id, top_n=limit)
    except Exception:
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
            for bid, score in (recommend_scored or [])[:10]:
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
    current_user: str = Depends(verify_jwt),
):
    if current_user != user_id:
        raise HTTPException(403, "Cannot access other user's home")

    sb = get_supabase()

    us_res = sb.table("user_state").select(
        "current_tier,total_likes,top_authors,top_l1s,updated_at"
    ).eq("user_id", user_id).limit(1).execute()

    us = (us_res.data[0] if us_res.data else None) or {
        "current_tier": 0, "total_likes": 0,
        "top_authors": [], "top_l1s": [], "updated_at": "",
    }
    tier = us["current_tier"]
    total_likes = us["total_likes"]
    top_authors = [a["author"] for a in (us.get("top_authors") or [])]
    top_l1s = [l["l1"] for l in (us.get("top_l1s") or [])]

    # stage
    stage_res = sb.table("recommendation_stage").select("current_stage").eq("id", 1).limit(1).execute()
    stage = (stage_res.data[0]["current_stage"] if stage_res.data else 0)

    # home_section_cache 확인
    hour_bucket = current_hour_bucket()
    input_hash = compute_home_input_hash(us.get("updated_at", ""), hour_bucket)
    cache = load_home_cache(user_id)

    if cache and cache.get("input_hash") == input_hash:
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
    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding,updated_at"
    ).eq("user_id", user_id).order("updated_at", desc=True).execute()
    user_books = ub_res.data or []

    themes_res = sb.table("curation_themes").select(
        "id,theme_type,title,description,personalization,"
        "target_l1,target_author,target_keyword,priority,click_rate"
    ).eq("is_active", True).execute()
    active_themes = themes_res.data or []

    theme_ids = [t["id"] for t in active_themes]
    cache_rows = []
    if theme_ids:
        cache_res = sb.table("curation_cache").select(
            "curation_id,book_ids,expires_at"
        ).in_("curation_id", theme_ids).execute()
        cache_rows = cache_res.data or []

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    curation_cache_by_id = {
        r["curation_id"]: r["book_ids"]
        for r in cache_rows
        if r.get("expires_at", "") > now_iso
    }

    fb_res = sb.table("fallback_curation").select("book_id").order("rank").limit(30).execute()
    fallback_books = fb_res.data or []

    seven_days_ago = (now - timedelta(days=7)).isoformat()
    uch_res = sb.table("user_curation_history").select("curation_id").eq(
        "user_id", user_id
    ).gte("shown_at", seven_days_ago).execute()
    recent_curation_ids = {r["curation_id"] for r in (uch_res.data or [])}

    # Tier 2 라면 recommend_core 호출
    recommend_scored = None
    if tier == 2:
        liked_books: dict = {}
        fb_data: dict = {}
        for ub in user_books:
            bid = ub["book_id"]
            liked_books[bid] = {"rating": ub.get("rating", "neutral")}
            fb_emb = ub.get("feedback_embedding")
            if fb_emb:
                fb_data[bid] = {"emb": to_np(fb_emb), "is_dislike": ub.get("rating") == "bad"}
        try:
            recommend_scored = compute_scored_books(
                index=request.app.state.index,
                liked_books=liked_books, fb_data=fb_data,
                prestacked_reasons=request.app.state.prestacked_reasons,
                desc_matrix_f16=request.app.state.desc_matrix_f16,
                agg_reason_matrix_f16=request.app.state.agg_reason_matrix_f16,
                bid_order=request.app.state.bid_order,
            )
        except Exception as e:
            print(f"recommend_scored failed for {user_id}: {e}")
            recommend_scored = []

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

    # BackgroundTasks: cache write + impression INSERT + user_curation_history
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
