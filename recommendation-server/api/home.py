"""recommendation-server/api/home.py

/home/{user_id} — User Tier 분기 후 섹션 조립. Spec §6.2.

쿼리 수: user_state 1 + user_books 1 + active themes 1 + curation_cache IN-clause 1
+ recommendation_stage 1 (+ fallback_curation 1)
"""
from __future__ import annotations
import random
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
from engine.cache import (load_cache, compute_input_hash, load_signals,
                          recompute_recommendations, rec_cache_reusable)
from engine.dedup import dedup_by_work, dedup_similar

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
    if not meta.get("cover_url"):
        # 홈=비주얼 서가(핵심가치 1). 커버 없는(minimal) 책은 홈 표면에서 제외하고
        # 호출측 fill 루프가 다음 후보로 채운다(실측: 커버 없는 카드가 홈을 깨보이게 함).
        # 데이터/추천 자체에는 무접촉 — /recommend·/similar API 는 그대로 노출.
        return None
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
        # over-fetch: 판본 dedup + 커버 없는 책 skip 후에도 limit 개가 남도록 넉넉히.
        raw = index.similar_by_desc(seed_book_id, limit=limit * 3 + 5)
        results = dedup_similar(raw, books_meta, seed_book_id, limit * 2)
    except Exception as e:
        print(f"[home] similar_by_desc failed (seed={seed_book_id}): {e}", flush=True)
        return []
    out = []
    for bid, _score in results:
        b = _book_dict(bid, books_meta)
        if b:
            out.append(b)
        if len(out) >= limit:
            break
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
    """Tier별 섹션 구성 규칙에 따라 실제 books 리스트를 조립한다.

    섹션 간 책 중복 제거: 같은 책이 인접 섹션에 반복 노출되면(실측: '한강 컬렉션'
    직후 '화제의 책'에 같은 책 2권) 화면 다양성이 무너진다 — 템플릿 순서 = 우선순위로
    앞 섹션에 나온 책은 뒤 섹션에서 제외(후보가 넉넉한 소스는 다음 후보로 채움).
    """
    templates = sections_for_tier(tier)
    sections: list[dict] = []
    seen_bids: set = set()
    # 같은 요청 안에서 두 큐레이션 슬롯이 같은 테마를 뽑지 않게 — 뽑힌 테마는
    # 이후 슬롯의 recent 제외 집합에 합류(tier2 두 번째 슬롯 활성화로 실제 가능해짐).
    picked_theme_ids: set = set()

    def _register(section: dict) -> dict:
        seen_bids.update(b["book_id"] for b in section.get("books", []))
        if section.get("curation_id"):
            picked_theme_ids.add(section["curation_id"])
        return section

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
            for bid, score in scored:  # 커버 없는/ghost 는 건너뛰고 후보에서 10개 채움
                b = _book_dict(bid, books_meta, score=score)
                if b:
                    books.append(b)
                if len(books) >= 10:
                    break
            sections.append(_register({
                "id": section_id, "type": "personal_recommend",
                "title": "당신을 위한 추천", "books": books,
                "algorithm_version": "h10_stage0",
            }))

        elif stype == "similar":
            if latest_liked_bid and latest_liked_bid in books_meta:
                seed_title = books_meta[latest_liked_bid].get("title", "")
                books = [b for b in
                         _similar_books_from_seed(index, books_meta, latest_liked_bid)
                         if b["book_id"] not in seen_bids]
                sections.append(_register({
                    "id": section_id, "type": "similar",
                    "title": similar_section_title(seed_title),
                    "seed_book_id": latest_liked_bid,
                    "books": books,
                }))
            else:
                # fallback: general curation
                sections.append(_register(_sample_curation(
                    active_themes, top_authors, top_l1s, tier,
                    recent_curation_ids | picked_theme_ids,
                    curation_cache_by_id, books_meta, personalization_override="general",
                    section_id=section_id, exclude_bids=seen_bids,
                )))

        elif stype == "curation":
            sections.append(_register(_sample_curation(
                active_themes, top_authors, top_l1s, tier,
                recent_curation_ids | picked_theme_ids,
                curation_cache_by_id, books_meta,
                personalization_override=tpl.get("personalization"),
                section_id=section_id, exclude_bids=seen_bids,
            )))

        elif stype == "trending":
            # 셔플(2026-07-02 Eden 지시): 과거 rank순 10권 고정 → 30권 풀에서
            # rank-가중 랜덤 비복원 샘플. 상위 인기권일수록 자주 보이되 조립(새로고침/
            # hour-bucket)마다 구성이 달라진다 — 고정 anchor 라 "매번 똑같다"던
            # 지루함 해소. 가중치=선형 감쇠(1위 n … 꼴찌 1), Efraimidis-Spirakis
            # (key U^(1/w) 내림차순 정렬 = 가중 비복원 샘플과 동치).
            n = len(fallback_books)
            weighted = sorted(
                enumerate(fallback_books),
                key=lambda t: random.random() ** (1.0 / max(n - t[0], 1)),
                reverse=True,
            )
            books = []
            for _, row in weighted:
                if row["book_id"] in seen_bids:
                    continue
                b = _book_dict(row["book_id"], books_meta)
                if b:
                    books.append(b)
                if len(books) >= 10:
                    break
            sections.append(_register({
                "id": section_id, "type": "trending",
                "title": "화제의 책", "books": books,
            }))

        elif stype == "category_nav":
            sections.append({
                "id": section_id, "type": "category_nav", "books": [],
            })

    return sections


def _sample_curation(
    active_themes, top_authors, top_l1s, tier, recent_ids,
    cache_by_id, books_meta, *, personalization_override=None, section_id,
    exclude_bids=None,
) -> dict:
    exclude_bids = exclude_bids or set()
    # 렌더 가능한 테마만 샘플링 — 갓 생성돼 curation_cache 가 아직 없는 테마(다음
    # hourly cron 에 채워짐)를 뽑으면 빈 섹션→드롭으로 홈이 빈약해진다(실측:
    # author 테마 대량 재생성 직후). 캐시 유무는 뽑기 전에 거른다.
    pool = [t for t in active_themes if cache_by_id.get(t["id"])]
    # 개인화 필터
    pool = [t for t in pool
            if personalization_override is None
            or t.get("personalization") == personalization_override]
    pool = filter_by_personalization(pool, tier=tier,
                                     top_authors=top_authors, top_l1s=top_l1s)
    pool = apply_recent_discount(pool, recent_ids)

    # 개인화 풀이 비면 general 폴백 (역시 렌더 가능한 테마만).
    # "tier2+" 포함 — tier2 템플릿의 두 번째 큐레이션 슬롯인데 tier2+ 테마가 DB 에
    # 하나도 없어 항상 빈 섹션→드롭, tier2 유저는 큐레이션이 실질 1칸뿐이던 버그
    # (Eden "실제론 1개만 랜더마이즈?" 리포트로 발견).
    if not pool and personalization_override in ("by_author", "by_l1", "tier2+"):
        pool = [t for t in active_themes
                if t.get("personalization") == "general" and cache_by_id.get(t["id"])]
        pool = apply_recent_discount(pool, recent_ids)

    picked = weighted_sample_one(pool)
    if picked is None:
        return {"id": section_id, "type": "curation", "books": []}

    book_ids = cache_by_id.get(picked["id"], [])
    books: list[dict] = []
    for bid in book_ids:  # 앞 섹션과 중복 제외하며 10개 채움
        if bid in exclude_bids:
            continue
        b = _book_dict(bid, books_meta)
        if b:
            books.append(b)
        if len(books) >= 10:
            break

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
        "book_id,rating,feedback_embedding,emotion_tags,review_text,updated_at,status"
    ).eq("user_id", user_id).order("updated_at", desc=True).execute().data,
        default=[]) or []

    # 행동 신호(관심없음) — rec 캐시 해시 정합 + personal_recommend 즉시 필터.
    signals = _safe(lambda: load_signals(sb, user_id), default=[]) or []
    ni_ids = {s["book_id"] for s in signals if s.get("signal") == "not_interested"}

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
        ub_hash = compute_input_hash(user_books, signals)
        built_at = getattr(request.app.state, "built_at", None) or ""
        # computed_at > built_at 포함(rec_cache_reusable) — /recommend 와 동일 기준. 이게
        # 없으면 인덱스 재빌드 후에도 /home 이 옛 인덱스로 계산된 추천을 계속 서빙한다
        # (실측: Eden 추천이 인덱스 빌드 이전 계산본이라 stale 서빙). 재빌드 시 재계산 유도.
        if rec_cache_reusable(rec_cache, ub_hash, built_at):
            # NI 필터: 신호 직후 재계산 완료 전에도 즉시 사라지게 (/recommend 와 동일).
            recommend_scored = [(r["book_id"], r.get("score", 0.0))
                                for r in rec_cache["recommendations"]
                                if r["book_id"] not in ni_ids]
        else:
            # 캐시 없음/stale → 요청경로에서 스코어링하지 않는다(무료 단일 CPU 8~17s 블로킹
            # 금지 — /recommend 와 동일 원칙). 백그라운드 재계산을 트리거하고 이번 응답은
            # personal_recommend 를 비운다(trending/curation 으로 fallback). 다음 로드에서
            # warm 캐시로 채워진다. 좋아요 변경 시엔 /recompute 가 선제로 이미 돌고 있어
            # 대개 곧 준비된다. (인라인 스코어링이 /home 을 8~17s 막고, 타임아웃 시 큐레이션
            # 섹션까지 사라지던 원인 제거.)
            background_tasks.add_task(
                recompute_recommendations, user_id, request.app.state)
            recommend_scored = []
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
