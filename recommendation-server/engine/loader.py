# recommendation-server/engine/loader.py
"""서버 시작 시 Supabase에서 벡터 데이터를 로드하여 VectorIndex 구축."""
import numpy as np
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from engine.index import VectorIndex


def _to_np(vec) -> np.ndarray:
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def _paginate(table_query, select_cols):
    """Supabase 테이블을 1000행씩 페이지네이션으로 전체 로드."""
    rows = []
    offset = 0
    while True:
        batch = table_query.select(select_cols).range(offset, offset + 999).execute()
        rows.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000
    return rows


def load_index() -> tuple[VectorIndex, dict]:
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1. books 메타정보
    books_raw = _paginate(sb.table("books"), "id,title,author,cover_url")
    books_meta = {b["id"]: {"title": b["title"], "author": b["author"],
                             "cover_url": b.get("cover_url")} for b in books_raw}

    # 2. genre_embeddings
    ge_raw = sb.table("genre_embeddings").select("id,embedding").execute()
    genre_embs = {g["id"]: _to_np(g["embedding"]) for g in ge_raw.data}

    # 3. book_v3_vectors
    v3_raw = _paginate(sb.table("book_v3_vectors"), "book_id,desc_embedding,l1_genre_id,l2_genre_id")
    v3_map = {v["book_id"]: v for v in v3_raw}

    # 4. book_love_reasons
    reasons_raw = _paginate(
        sb.table("book_love_reasons").not_.is_("reason_embedding", "null"),
        "book_id,reason_embedding"
    )
    reasons_by_book: dict[str, list[np.ndarray]] = {}
    for r in reasons_raw:
        bid = r["book_id"]
        if bid not in reasons_by_book:
            reasons_by_book[bid] = []
        reasons_by_book[bid].append(_to_np(r["reason_embedding"]))

    # 5. VectorIndex 구축
    index = VectorIndex(dim=2000)
    loaded = 0
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            continue
        l1_id, l2_id = v3.get("l1_genre_id"), v3.get("l2_genre_id")
        if not l1_id or not l2_id or l1_id not in genre_embs or l2_id not in genre_embs:
            continue
        desc_emb = v3.get("desc_embedding")
        if not desc_emb:
            continue
        index.add_book(bid, reasons=reasons_by_book.get(bid, []),
                       desc=_to_np(desc_emb), l1=genre_embs[l1_id], l2=genre_embs[l2_id])
        loaded += 1

    index.build_desc_matrix()
    print(f"[loader] {loaded} books loaded into VectorIndex")
    return index, books_meta
