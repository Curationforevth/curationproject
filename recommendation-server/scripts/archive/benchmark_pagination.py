#!/usr/bin/env python3
"""Pagination 속도 벤치마크.

검증 항목:
  - page_size 별 fetch 속도 비교 (500, 1000, 2000, 5000)
  - 4개 테이블 순차 vs 병렬 fetch 속도 비교

사용법: cd recommendation-server && python scripts/benchmark_pagination.py
필요: .env (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
"""
from __future__ import annotations

import os
import sys
import time
import asyncio
import concurrent.futures

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def fetch_table(table: str, select: str, page_size: int,
                order_col: str = "id", filters: dict | None = None) -> tuple[int, float]:
    """테이블에서 전체 데이터를 page_size로 fetch. (row_count, elapsed_sec) 반환."""
    all_rows = 0
    offset = 0
    t0 = time.perf_counter()
    pages = 0
    while True:
        q = sb.table(table).select(select).order(order_col).range(
            offset, offset + page_size - 1)
        if filters:
            for col, condition in filters.items():
                q = q.filter(col, *condition)
        rows = q.execute().data
        all_rows += len(rows)
        pages += 1
        if len(rows) < page_size:
            break
        offset += page_size
    elapsed = time.perf_counter() - t0
    return all_rows, elapsed, pages


# 테이블 정의
TABLES = [
    ("books", "id,title,author,cover_url", "id", None),
    ("genre_embeddings", "id,embedding", "id", None),
    ("book_v3_vectors", "book_id,desc_embedding,l1_genre_id,l2_genre_id", "book_id", None),
    ("book_love_reasons", "book_id,reason_embedding", "id",
     {"reason_embedding": ("not.is", "null")}),
]

PAGE_SIZES = [500, 1000, 2000, 5000]


def run_sequential_benchmark():
    """page_size 별 순차 fetch 벤치마크."""
    print("\n" + "=" * 70)
    print("1. Page size 별 순차 fetch 벤치마크")
    print("=" * 70)

    for page_size in PAGE_SIZES:
        print(f"\n--- page_size = {page_size} ---")
        total_time = 0
        for table, select, order_col, filters in TABLES:
            rows, elapsed, pages = fetch_table(table, select, page_size, order_col, filters)
            total_time += elapsed
            print(f"  {table:25s}: {rows:>6} rows, {pages:>3} pages, {elapsed:.2f}s")
        print(f"  {'TOTAL':25s}: {total_time:.2f}s")


def run_parallel_benchmark():
    """4개 테이블 병렬 fetch 벤치마크 (best page_size 사용)."""
    print("\n" + "=" * 70)
    print("2. 병렬 fetch 벤치마크 (page_size=1000)")
    print("=" * 70)

    page_size = 1000  # 안전한 기본값

    def fetch_one(args):
        table, select, order_col, filters = args
        # 병렬이므로 각각 별도 클라이언트 사용
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        all_rows = 0
        offset = 0
        t0 = time.perf_counter()
        pages = 0
        while True:
            q = client.table(table).select(select).order(order_col).range(
                offset, offset + page_size - 1)
            if filters:
                for col, condition in filters.items():
                    q = q.filter(col, *condition)
            rows = q.execute().data
            all_rows += len(rows)
            pages += 1
            if len(rows) < page_size:
                break
            offset += page_size
        elapsed = time.perf_counter() - t0
        return table, all_rows, elapsed, pages

    # 병렬 실행
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_one, t) for t in TABLES]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    total_parallel = time.perf_counter() - t0

    for table, rows, elapsed, pages in sorted(results, key=lambda x: x[0]):
        print(f"  {table:25s}: {rows:>6} rows, {pages:>3} pages, {elapsed:.2f}s")
    print(f"  {'WALL CLOCK':25s}: {total_parallel:.2f}s")

    # 순차 비교
    print(f"\n--- 순차 대비 (page_size=1000) ---")
    t0 = time.perf_counter()
    for table, select, order_col, filters in TABLES:
        fetch_table(table, select, page_size, order_col, filters)
    total_seq = time.perf_counter() - t0
    print(f"  순차: {total_seq:.2f}s")
    print(f"  병렬: {total_parallel:.2f}s")
    print(f"  개선: {total_seq / total_parallel:.1f}x")


if __name__ == "__main__":
    print("=" * 70)
    print("Pagination 속도 벤치마크")
    print("=" * 70)

    run_sequential_benchmark()
    run_parallel_benchmark()

    print("\n" + "=" * 70)
    print("벤치마크 완료")
    print("=" * 70)
