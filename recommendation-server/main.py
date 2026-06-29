from __future__ import annotations

import os
import psutil
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from api.recommend import router as recommend_router
from api.similar import router as similar_router
from api.feedback import router as feedback_router
from api.home import router as home_router
from api.curation import router as curation_router

# 배포 검증용 코드 리비전 마커. /health 로 어떤 코드가 라이브인지 관측한다.
# home-harden: 앱이 /home 을 직접 렌더(큐레이션/트렌딩 노출)하게 되므로 견고화 —
# 빈 책-섹션 제거(앱 빈 줄 방지) + Supabase 쿼리 _safe 래핑(한 쿼리 실패해도 500 X).
# 직전 feedback-async·dedup-works·cache-livehash 포함.
CODE_REV = "oom-mem-relief-20260629"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from engine.loader import load_index
    index, books_meta, built_at, prestacked, desc_mat, agg_mat, bid_order = load_index()
    app.state.index = index
    app.state.books_meta = books_meta
    app.state.built_at = built_at
    app.state.prestacked_reasons = prestacked
    app.state.desc_matrix_f16 = desc_mat
    app.state.agg_reason_matrix_f16 = agg_mat
    app.state.bid_order = bid_order
    # /health 관측용 카운트는 시작 시 1회만 계산해 저장한다. (과거: health 가 v4
    # 전용 bid_order/prestacked 를 읽어 v3 배포에선 books_loaded=0, total_reasons=0
    # 으로 보고 → 엔진이 실제 로드됐는지 /health 로 확인 불가, code_rev 만 보고
    # "라이브"로 오판하는 원인이었다. book_ids/reasons 는 v3·v4 공통 소스다.)
    # desc 행렬을 시작 시 1회 빌드 — Tier2 two-stage 선필터 + /similar 가 재사용.
    # (요청 중에 빌드하면 이벤트루프 블로킹.)
    index.build_desc_matrix()
    # dead l1/l2(W_L1=W_L2=0) 단일 zero 공유로 회수 — 재빌드 없이 현재 인덱스에 즉시
    # 적용(무료 512MB OOM 완화). build_desc_matrix 는 desc 만 쓰므로 순서 무관.
    index.strip_unused_genre_vectors()
    v4 = prestacked is not None
    app.state.books_loaded = len(index.book_ids)
    if v4:
        app.state.total_reasons = sum(len(r) for r in prestacked.values())
    else:
        app.state.total_reasons = sum(
            len(index.get_book(bid).reasons) for bid in index.book_ids
        )
    print(
        f"[main] Server ready. {app.state.books_loaded} books, "
        f"{app.state.total_reasons} reasons. v4={v4}. Built at {built_at}"
    )
    yield


app = FastAPI(title="Curation Recommendation Server", lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(similar_router)
app.include_router(feedback_router)
app.include_router(home_router)
app.include_router(curation_router)


@app.get("/health")
async def health(request: Request):
    state = request.app.state
    ver = "v4-prestacked" if getattr(state, "prestacked_reasons", None) else "v3-float16"
    mem_mb = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
    return {
        "status": "ok",
        "version": ver,
        "code_rev": CODE_REV,
        "books_loaded": getattr(state, "books_loaded", 0),
        "total_reasons": getattr(state, "total_reasons", 0),
        "index_built_at": getattr(state, "built_at", None),
        "memory_mb": mem_mb,
        "cache_hits": getattr(state, "cache_hits", 0),
        "cache_misses": getattr(state, "cache_misses", 0),
    }
