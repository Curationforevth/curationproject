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
# stage1-topn-700: 후보 150→700(Eden 승인, 현실형 recall 95→98.9%) + stage2
# 후보 블록 처리(무분할 transient 175MB→40MB). 직전 scorer-reduceat-fix(PR#38).
CODE_REV = "trending-shuffle-20260702"


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
    # desc 행렬: 번들 desc_matrix_f16 을 그대로 attach(중복 빌드 회피 — desc 1벌, ~72MB
    # 절감, 무료 512MB). v4 번들엔 desc_mat 존재. 구 v3/번들 없으면 per-book 으로 빌드(폴백).
    if desc_mat is not None and bid_order is not None:
        index.attach_desc_matrix(desc_mat, bid_order)
    else:
        index.build_desc_matrix()
    # dead l1/l2(W_L1=W_L2=0) 단일 zero 공유로 회수(무료 512MB).
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
        # 마지막 recompute 의 스테이지별 소요시간 — prod 병목 분해 관측용(로그 접근 불필요).
        "last_recompute_timings": getattr(state, "last_recompute_timings", None),
    }
