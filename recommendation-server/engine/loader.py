# recommendation-server/engine/loader.py
"""서버 시작 시 Supabase에서 벡터 데이터를 로드하여 VectorIndex 구축.

RPC 함수(export_book_vectors, export_reasons_batch)를 사용하여
REST API 호출 횟수를 최소화한다.

필요한 Supabase RPC:
  - export_book_vectors(): books + v3_vectors + genre_embeddings 반환
  - export_reasons_batch(p_offset, p_limit): reason 임베딩 배치 반환
"""
import numpy as np
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from engine.index import VectorIndex


def _to_np(vec) -> np.ndarray:
    """DB 벡터(리스트 또는 문자열)를 L2-정규화된 numpy float32로 변환."""
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def load_index() -> tuple[VectorIndex, dict]:
    """Supabase RPC로 전체 벡터 로드 → VectorIndex + books_meta 반환."""
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1. books + v3_vectors + genres (RPC 1회)
    print("[loader] Loading books, v3 vectors, genres...")
    data = sb.rpc("export_book_vectors").execute().data

    books_meta = {}
    for b in (data.get("books") or []):
        books_meta[b["id"]] = {
            "title": b["title"], "author": b["author"],
            "cover_url": b.get("cover"),
        }

    genre_embs = {}
    for g in (data.get("genres") or []):
        genre_embs[g["id"]] = _to_np(g["emb"])

    v3_map = {}
    for v in (data.get("v3") or []):
        v3_map[v["book_id"]] = v

    print(f"  books={len(books_meta)}, genres={len(genre_embs)}, v3={len(v3_map)}")

    # 2. reason 임베딩 (RPC 배치, 5000건씩)
    print("[loader] Loading reason embeddings...")
    reasons_by_book: dict[str, list[np.ndarray]] = {}
    offset = 0
    batch_size = 5000
    total_reasons = 0
    while True:
        batch = sb.rpc("export_reasons_batch", {
            "p_offset": offset, "p_limit": batch_size
        }).execute().data
        if not batch:
            break
        for r in batch:
            bid = r["bid"]
            if bid not in reasons_by_book:
                reasons_by_book[bid] = []
            reasons_by_book[bid].append(_to_np(r["e"]))
            total_reasons += 1
        print(f"  ... {total_reasons} reasons loaded")
        if len(batch) < batch_size:
            break
        offset += batch_size

    print(f"  total reasons={total_reasons}, books with reasons={len(reasons_by_book)}")

    # 3. VectorIndex 구축
    index = VectorIndex(dim=2000)
    loaded = 0
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            continue
        l1_id, l2_id = v3.get("l1"), v3.get("l2")
        if not l1_id or not l2_id or l1_id not in genre_embs or l2_id not in genre_embs:
            continue
        desc_emb = v3.get("desc")
        if not desc_emb:
            continue
        index.add_book(
            bid,
            reasons=reasons_by_book.get(bid, []),
            desc=_to_np(desc_emb),
            l1=genre_embs[l1_id],
            l2=genre_embs[l2_id],
        )
        loaded += 1

    index.build_desc_matrix()
    print(f"[loader] {loaded} books loaded into VectorIndex")
    return index, books_meta
