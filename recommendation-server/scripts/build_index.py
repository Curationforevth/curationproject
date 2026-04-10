#!/usr/bin/env python3
"""Supabase에서 벡터 데이터를 로드하여 data/index.pkl 생성.

사용법: cd recommendation-server && python scripts/build_index.py
결과물: data/index.pkl (~170MB, float16)
"""
from __future__ import annotations

import os
import sys
import time
import pickle
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from engine.index import VectorIndex
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, EMBEDDING_DIMENSIONS

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "index.pkl")
PAGE_SIZE_META = 1000
PAGE_SIZE_VECTOR = 500
PAGE_SIZE_REASONS = 200  # book_love_reasons 전용 (38K+ rows, timeout 방지)
# NOTE: recommendation-server 는 scripts/lib 와 별도 패키지.
# scripts.lib.retry.with_retry 와 의도적으로 독립된 retry 로직 사용.
# 변경 시 scripts/lib/retry.py 의 SQLSTATE whitelist 와 동기화 필요 없음
# (이 파일은 read-only fetch 만 하므로 SQLSTATE 분기 불필요).
MAX_RETRIES = 3
RETRY_BACKOFF = 10
PAGE_SLEEP = 1


def _to_np(vec) -> np.ndarray:
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def _fetch_paginated(sb, table: str, select: str, page_size: int,
                     order_col: str = "id", filters=None) -> list:
    all_rows = []
    offset = 0
    while True:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                q = sb.table(table).select(select).order(order_col).range(
                    offset, offset + page_size - 1)
                if filters:
                    for col, condition in filters.items():
                        q = q.filter(col, *condition)
                rows = q.execute().data
                break
            except Exception as e:
                print(f"  [retry {attempt}/{MAX_RETRIES}] {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BACKOFF * attempt)
        all_rows.extend(rows)
        print(f"  page {offset // page_size + 1}: {len(rows)} rows (total: {len(all_rows)})")
        if len(rows) < page_size:
            break
        offset += page_size
        time.sleep(PAGE_SLEEP)
    return all_rows


def build(dry_run: bool = False):
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1. books meta
    print("[build] Loading books meta...")
    books_raw = _fetch_paginated(sb, "books", "id,title,author,cover_url", PAGE_SIZE_META)
    books_meta = {}
    for b in books_raw:
        books_meta[b["id"]] = {
            "title": b["title"], "author": b["author"],
            "cover_url": b.get("cover_url"),
        }
    print(f"  → {len(books_meta)} books")

    # 2. genre embeddings
    print("[build] Loading genre embeddings...")
    genres_raw = _fetch_paginated(sb, "genre_embeddings", "id,embedding", PAGE_SIZE_VECTOR)
    genre_embs = {}
    for g in genres_raw:
        emb = _to_np(g["embedding"])
        assert emb.shape[0] == EMBEDDING_DIMENSIONS, \
            f"genre dim mismatch: {emb.shape[0]} != {EMBEDDING_DIMENSIONS}"
        genre_embs[g["id"]] = emb
    print(f"  → {len(genre_embs)} genres")

    # 3. v3 vectors
    print("[build] Loading v3 vectors...")
    v3_raw = _fetch_paginated(
        sb, "book_v3_vectors", "book_id,desc_embedding,l1_genre_id,l2_genre_id",
        PAGE_SIZE_VECTOR, order_col="book_id")
    v3_map = {}
    for v in v3_raw:
        v3_map[v["book_id"]] = v
    print(f"  → {len(v3_map)} v3 vectors")

    # 4. reason embeddings
    print("[build] Loading reason embeddings...")
    reasons_raw = _fetch_paginated(
        sb, "book_love_reasons", "book_id,reason_embedding",
        PAGE_SIZE_REASONS,
        filters={"reason_embedding": ("not.is", "null")})
    reasons_by_book = {}
    for r in reasons_raw:
        if r.get("reason_embedding") is not None:
            bid = r["book_id"]
            emb = _to_np(r["reason_embedding"])
            assert emb.shape[0] == EMBEDDING_DIMENSIONS, \
                f"reason dim mismatch: {emb.shape[0]} != {EMBEDDING_DIMENSIONS}"
            if bid not in reasons_by_book:
                reasons_by_book[bid] = []
            reasons_by_book[bid].append(emb)
    total_reasons = sum(len(v) for v in reasons_by_book.values())
    print(f"  → {total_reasons} reasons across {len(reasons_by_book)} books")

    # 5. VectorIndex 구축 (float16)
    print("[build] Building VectorIndex (float16)...")
    index = VectorIndex(dim=EMBEDDING_DIMENSIONS, dtype=np.float16)
    loaded = 0
    skipped = 0
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            skipped += 1
            continue
        l1_id, l2_id = v3.get("l1_genre_id"), v3.get("l2_genre_id")
        if not l1_id or not l2_id or l1_id not in genre_embs or l2_id not in genre_embs:
            skipped += 1
            continue
        desc_emb = v3.get("desc_embedding")
        if not desc_emb:
            skipped += 1
            continue
        desc_np = _to_np(desc_emb)
        assert desc_np.shape[0] == EMBEDDING_DIMENSIONS, \
            f"desc dim mismatch: {desc_np.shape[0]} != {EMBEDDING_DIMENSIONS}"
        index.add_book(
            bid,
            reasons=reasons_by_book.get(bid, []),
            desc=desc_np,
            l1=genre_embs[l1_id],
            l2=genre_embs[l2_id],
        )
        loaded += 1

    index.build_desc_matrix()
    print(f"  → {loaded} books loaded, {skipped} skipped")

    # C3 (H2): skip ratio guard. 임계값 초과 시 exit 1 — 데이터 품질 저하된
    # 인덱스가 silent 하게 prod 에 배포되지 않도록 방지.
    SKIP_RATIO_THRESHOLD = 0.05  # 5%
    total = loaded + skipped
    if total > 0:
        skip_ratio = skipped / total
        print(f"  skip ratio: {skip_ratio:.1%}")
        if skip_ratio > SKIP_RATIO_THRESHOLD:
            print(
                f"❌ skip ratio {skip_ratio:.1%} > {SKIP_RATIO_THRESHOLD:.1%} — build 실패",
                file=sys.stderr,
            )
            sys.exit(1)

    # 6. pkl 저장 — C2 (H3): tmp + os.replace 로 atomic write.
    # 서버가 load 중일 때 half-written pkl 을 읽지 않도록 보장.
    if dry_run:
        print(f"\n[dry-run] index.pkl 저장 건너뜀")
        print(f"  books: {loaded}")
        print(f"  reasons: {total_reasons}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    built_at = datetime.now(timezone.utc).isoformat()
    bundle = {
        "index": index,
        "meta": books_meta,
        "built_at": built_at,
        "version": "v3-float16",
    }
    tmp_path = OUTPUT_PATH + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(bundle, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, OUTPUT_PATH)

    sha = hashlib.sha256()
    with open(OUTPUT_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    hash_path = OUTPUT_PATH + ".sha256"
    tmp_hash = hash_path + ".tmp"
    with open(tmp_hash, "w") as f:
        f.write(sha.hexdigest())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_hash, hash_path)

    size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    print(f"\n[build] Done! {OUTPUT_PATH}")
    print(f"  size: {size_mb:.1f} MB")
    print(f"  books: {loaded}")
    print(f"  reasons: {total_reasons}")
    print(f"  built_at: {built_at}")
    print(f"  version: v3-float16")
    print(f"  sha256: {sha.hexdigest()}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="DB에서 읽기만 하고 index.pkl 저장 안 함")
    args = p.parse_args()
    build(dry_run=args.dry_run)
