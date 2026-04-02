from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.recommend import router as recommend_router
from api.similar import router as similar_router
from api.feedback import router as feedback_router

app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from engine.loader import load_index
    index, books_meta = load_index()
    app_state["index"] = index
    app_state["books_meta"] = books_meta
    print(f"[main] Server ready. {len(index.book_ids)} books in index.")
    yield
    app_state.clear()


app = FastAPI(title="Curation Recommendation Server", lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(similar_router)
app.include_router(feedback_router)


@app.get("/health")
async def health():
    index = app_state.get("index")
    return {"status": "ok", "books_loaded": len(index.book_ids) if index else 0}
