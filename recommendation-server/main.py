from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from api.recommend import router as recommend_router
from api.similar import router as similar_router
from api.feedback import router as feedback_router


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
    v4 = prestacked is not None
    print(f"[main] Server ready. {len(index.book_ids)} books. v4={v4}. Built at {built_at}")
    yield


app = FastAPI(title="Curation Recommendation Server", lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(similar_router)
app.include_router(feedback_router)


@app.get("/health")
async def health(request: Request):
    index = getattr(request.app.state, "index", None)
    total_reasons = 0
    if index:
        for bv in index._books.values():
            total_reasons += len(bv.reasons)
    built_at = getattr(request.app.state, "built_at", "")
    return {
        "status": "ok",
        "books_loaded": len(index.book_ids) if index else 0,
        "total_reasons": total_reasons,
        "index_built_at": built_at,
        "version": "v3-float16",
    }
