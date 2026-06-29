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
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from engine.index import VectorIndex
from config import (
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, EMBEDDING_DIMENSIONS,
    OPENAI_API_KEY, EMBEDDING_MODEL,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "index.pkl")
PAGE_SIZE_META = 1000
PAGE_SIZE_VECTOR = 1000
# NOTE: recommendation-server 는 scripts/lib 와 별도 패키지.
# scripts.lib.retry.with_retry 와 의도적으로 독립된 retry 로직 사용.
# 변경 시 scripts/lib/retry.py 의 SQLSTATE whitelist 와 동기화 필요 없음
# (이 파일은 read-only fetch 만 하므로 SQLSTATE 분기 불필요).
MAX_RETRIES = 3
RETRY_BACKOFF = 10
PAGE_SLEEP = 0  # read-only fetch — sleep 불필요
EMBED_BATCH = 128  # build 시점 reason 텍스트 배치 임베딩 크기


def _lean_strip_reasons(index, bid_order):
    """v4 lean: prestacked 구축 후 저장 index 의 per-book reasons 를 비운다.

    serving(twostage)은 prestacked_reasons 를 사용하고 index.reasons fallback 은
    prestacked 가 빈 책(=원래 reason 없는 책)에만 도달하므로, 저장 index 에 reason 을
    중복 보유할 필요가 없다. 무료 Render 512MB 에서 reason 중복(~142MB) 제거로 OOM 방지.
    desc/l1/l2 는 serving 이 index 에서 읽으므로 보존한다.
    """
    for bid in bid_order:
        bv = index.get_book(bid)
        if bv is not None:
            bv.reasons = []


def set_candidate_tiers(index, v3_map):
    """인덱스에 실제 add된 책 중 non-rich source_tier 만 index._candidate_tier 로 운반.

    sparse(rich=1.0 생략). 키는 bid_order(=index.book_ids) 부분집합 — skip(no_meta/no_desc)
    으로 빠진 책·인덱스 밖 책(ghost)은 제외(M5). build_desc_matrix 가 페널티/제외집합 파생.
    """
    book_ids = set(index.book_ids)
    tier_map = {}
    for bid, v3 in v3_map.items():
        if bid not in book_ids:
            continue
        t = v3.get("source_tier") or "rich"
        if t != "rich":
            tier_map[bid] = t
    index._candidate_tier = tier_map


def _to_np(vec) -> np.ndarray:
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """OpenAI 임베딩 배치 호출 (build 시점 reason 텍스트 임베딩).

    Supabase 의 reason_embedding 은 free-tier(500MB) 위해 비워둠(NULL). reason
    벡터를 Supabase 에 327MB 로 다시 저장하는 대신, build 시점에 텍스트를 임베딩해
    index.pkl 에만 싣는다 → 재빌드가 reason(제품 #1 차별점)을 잃지 않는다.
    api/feedback.py:_embed_text 와 동일 모델/차원(text-embedding-3-large, 2000d).
    """
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        chunk = texts[i:i + EMBED_BATCH]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": EMBEDDING_MODEL, "input": chunk,
                          "dimensions": EMBEDDING_DIMENSIONS},
                    timeout=60,
                )
                resp.raise_for_status()
                out.extend(d["embedding"] for d in resp.json()["data"])
                break
            except Exception as e:
                print(f"  [embed retry {attempt}/{MAX_RETRIES}] {e}", flush=True)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BACKOFF * attempt)
        if (i // EMBED_BATCH) % 10 == 0:
            done = min(i + EMBED_BATCH, len(texts))
            print(f"  [build] embedded reasons {done}/{len(texts)}", flush=True)
    return out


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


def build(dry_run: bool = False, incremental: bool = False):
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Incremental check: skip full rebuild if no changes since last build
    if incremental and os.path.exists(OUTPUT_PATH):
        print("[build] Incremental mode — checking for changes since last build...")
        with open(OUTPUT_PATH, "rb") as f:
            bundle = pickle.load(f)
        last_built_at = bundle.get("built_at", "")
        if last_built_at:
            print(f"  last_built_at: {last_built_at}")
            v3_changed = _fetch_paginated(
                sb, "book_v3_vectors", "book_id", PAGE_SIZE_VECTOR,
                order_col="book_id",
                filters={"updated_at": ("gt", last_built_at)})
            reasons_changed = _fetch_paginated(
                sb, "book_love_reasons", "book_id", PAGE_SIZE_VECTOR,
                filters={"updated_at": ("gt", last_built_at)})
            if len(v3_changed) == 0 and len(reasons_changed) == 0:
                print("No changes — skipping rebuild")
                return
            else:
                print(f"Changes detected (v3_vectors: {len(v3_changed)}, love_reasons: {len(reasons_changed)}) — running full rebuild")
        else:
            print("  no built_at in existing index — running full rebuild")

    # 1~4. 데이터 로드 — 2단계 병렬 (Supabase free tier connection 제한 고려)
    # Group A (가벼움): books meta + genre embeddings — 동시 fetch
    # Group B (무거움): v3 vectors + reason embeddings — 동시 fetch
    import concurrent.futures

    def _fetch_books_meta_task():
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("[build] Loading books meta...", flush=True)
        raw = _fetch_paginated(client, "books", "id,title,author,cover_url", PAGE_SIZE_META)
        meta = {}
        for b in raw:
            meta[b["id"]] = {
                "title": b["title"], "author": b["author"],
                "cover_url": b.get("cover_url"),
            }
        print(f"  → {len(meta)} books", flush=True)
        return meta

    def _fetch_genres_task():
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("[build] Loading genre embeddings...", flush=True)
        raw = _fetch_paginated(client, "genre_embeddings", "id,embedding", PAGE_SIZE_VECTOR)
        embs = {}
        for g in raw:
            emb = _to_np(g["embedding"])
            assert emb.shape[0] == EMBEDDING_DIMENSIONS
            embs[g["id"]] = emb
        print(f"  → {len(embs)} genres", flush=True)
        return embs

    def _fetch_v3_task():
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("[build] Loading v3 vectors...", flush=True)
        raw = _fetch_paginated(
            client, "book_v3_vectors",
            "book_id,desc_embedding,l1_genre_id,l2_genre_id,source_tier",
            PAGE_SIZE_VECTOR, order_col="book_id")
        v3 = {v["book_id"]: v for v in raw}
        print(f"  → {len(v3)} v3 vectors", flush=True)
        return v3

    def _fetch_reasons_task():
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("[build] Loading reason TEXT (build-time embedding)...", flush=True)
        # Supabase 의 reason_embedding 은 free-tier(500MB) 위해 NULL. reason 텍스트만
        # 읽어 build 시점에 임베딩한다 → 벡터는 pkl 에만 존재, 재빌드가 reason(제품 #1
        # 차별점)을 잃지 않는다. (과거: build 가 NULL reason_embedding 을 읽어 재빌드
        # 시 reason 0 이 되는 무음 소실 버그.)
        raw = _fetch_paginated(
            client, "book_love_reasons", "book_id,reason", PAGE_SIZE_VECTOR)
        pairs = [(r["book_id"], r["reason"]) for r in raw
                 if r.get("reason") and r["reason"].strip()]
        if not pairs:
            print("  → 0 reason texts found", flush=True)
            return {}
        if dry_run:
            # dry-run 은 비용 회피 — 경로 검증용으로 일부만 임베딩
            sample = pairs[:50]
            print(f"  [dry-run] {len(pairs)} reason texts found; "
                  f"embedding sample {len(sample)} to verify path", flush=True)
            pairs = sample
        texts = [t for _, t in pairs]
        print(f"  [build] embedding {len(texts)} reason texts...", flush=True)
        embs = _embed_batch(texts)
        by_book: dict = {}
        for (bid, _), emb in zip(pairs, embs):
            arr = _to_np(emb)
            assert arr.shape[0] == EMBEDDING_DIMENSIONS
            by_book.setdefault(bid, []).append(arr)
        total = sum(len(v) for v in by_book.values())
        print(f"  → {total} reasons across {len(by_book)} books "
              f"(build-time embedded)", flush=True)
        return by_book

    # Group A: 가벼운 테이블 2개 동시
    print("[build] Group A: books + genres (parallel)...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_meta = ex.submit(_fetch_books_meta_task)
        f_genres = ex.submit(_fetch_genres_task)
        books_meta = f_meta.result()
        genre_embs = f_genres.result()

    # Group B: 무거운 테이블 2개 동시
    print("[build] Group B: v3 + reasons (parallel)...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_v3 = ex.submit(_fetch_v3_task)
        f_reasons = ex.submit(_fetch_reasons_task)
        v3_map = f_v3.result()
        reasons_by_book = f_reasons.result()

    total_reasons = sum(len(v) for v in reasons_by_book.values())

    # reason 무음 소실 가드: reason 은 제품 #1 차별점(W_REASON). 실빌드에서 0 이면
    # (임베딩 실패/텍스트 누락) 중단해 reason 없는 인덱스가 prod 에 배포되지 않게 한다.
    if not dry_run and total_reasons == 0:
        print("❌ reason 0개 — build 중단(무음 reason 소실 방지). "
              "book_love_reasons.reason 텍스트 + OPENAI 임베딩 경로 확인.",
              file=sys.stderr)
        sys.exit(1)

    # 5. VectorIndex 구축 (float16)
    print("[build] Building VectorIndex (float16)...")
    index = VectorIndex(dim=EMBEDDING_DIMENSIONS, dtype=np.float16)
    loaded = 0
    skipped = 0
    skip_reasons = {"no_books_meta": 0, "no_genre": 0, "no_desc": 0}
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            skipped += 1
            skip_reasons["no_books_meta"] += 1
            continue
        l1_id, l2_id = v3.get("l1_genre_id"), v3.get("l2_genre_id")
        # l1/l2가 NULL이거나 genre_embs에 없으면 영벡터로 대체
        # (H10_no_l1에서 L1/L2 가중치 0이므로 영벡터 사용해도 스코어 영향 없음)
        zero_vec = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
        l1_emb = genre_embs.get(l1_id, zero_vec) if l1_id else zero_vec
        l2_emb = genre_embs.get(l2_id, zero_vec) if l2_id else zero_vec
        desc_emb = v3.get("desc_embedding")
        if not desc_emb:
            skipped += 1
            skip_reasons["no_desc"] += 1
            continue
        desc_np = _to_np(desc_emb)
        assert desc_np.shape[0] == EMBEDDING_DIMENSIONS, \
            f"desc dim mismatch: {desc_np.shape[0]} != {EMBEDDING_DIMENSIONS}"
        index.add_book(
            bid,
            reasons=reasons_by_book.get(bid, []),
            desc=desc_np,
            l1=l1_emb,
            l2=l2_emb,
        )
        loaded += 1

    set_candidate_tiers(index, v3_map)  # source_tier 운반(생존자만) → 페널티 파생
    index.build_desc_matrix()
    n_provisional = len(index._candidate_tier)
    print(f"  → {loaded} books loaded, {skipped} skipped")
    print(f"  skip breakdown: {skip_reasons}")
    print(f"  source_tier: rich={loaded - n_provisional}, non-rich(provisional)={n_provisional}")

    # 5b. v4 프리컴퓨팅 데이터 구성
    print("[build] Building v4 prestacked data...")
    bid_order = list(index._books.keys())

    prestacked_f16 = {}
    for bid in bid_order:
        bv = index.get_book(bid)
        if bv.reasons:
            prestacked_f16[bid] = np.stack(bv.reasons).astype(np.float16)
        else:
            prestacked_f16[bid] = np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float16)

    desc_matrix_f16 = np.stack([index.get_book(bid).desc for bid in bid_order]).astype(np.float16)

    agg_reason_f16_list = []
    for bid in bid_order:
        bv = index.get_book(bid)
        if bv.reasons:
            mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
            norm = np.linalg.norm(mean_vec)
            agg_reason_f16_list.append(
                (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16))
        else:
            agg_reason_f16_list.append(np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float16))
    agg_reason_matrix_f16 = np.stack(agg_reason_f16_list)
    print(f"  → desc_matrix: {desc_matrix_f16.shape}, agg_reason_matrix: {agg_reason_matrix_f16.shape}")

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

    # v4 lean: prestacked/agg 구축 완료 후 저장 index 의 reason 중복 제거(무료 512MB RSS).
    _lean_strip_reasons(index, bid_order)

    bundle = {
        "index": index,
        "meta": books_meta,
        "built_at": built_at,
        "version": "v4-prestacked",
        "prestacked_reasons_f16": prestacked_f16,
        "desc_matrix_f16": desc_matrix_f16,
        "agg_reason_matrix_f16": agg_reason_matrix_f16,
        "bid_order": bid_order,
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
    print(f"  version: v4-prestacked")
    print(f"  sha256: {sha.hexdigest()}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="DB에서 읽기만 하고 index.pkl 저장 안 함")
    p.add_argument("--incremental", action="store_true",
                   help="변경된 row가 없으면 rebuild 건너뜀 (updated_at 기반)")
    args = p.parse_args()
    build(dry_run=args.dry_run, incremental=args.incremental)
