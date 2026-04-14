#!/usr/bin/env python3
"""l1/l2가 NULL인 v3 책 512권 분석."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# v3에서 l1 또는 l2가 NULL인 book_id 수집
offset = 0
null_bids = []
while True:
    res = sb.table("book_v3_vectors").select("book_id").or_(
        "l1_genre_id.is.null,l2_genre_id.is.null"
    ).range(offset, offset + 999).execute()
    if not res.data:
        break
    null_bids.extend(r["book_id"] for r in res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

print(f"l1/l2 NULL인 v3 책: {len(null_bids)}권\n")

# 해당 책들의 books 테이블 정보 (genre, title, source 확인)
sample = null_bids[:50]
books_res = sb.table("books").select("id,title,author,genre,source").in_("id", sample).execute()

# genre 분포
genres = {}
sources = {}
for b in books_res.data:
    g = b.get("genre") or "(NULL)"
    s = b.get("source") or "(NULL)"
    genres[g] = genres.get(g, 0) + 1
    sources[s] = sources.get(s, 0) + 1

print("--- genre 분포 (상위 10) ---")
for g, c in sorted(genres.items(), key=lambda x: -x[1])[:10]:
    print(f"  {c:>3}권: {g[:60]}")

print(f"\n--- source 분포 ---")
for s, c in sorted(sources.items(), key=lambda x: -x[1]):
    print(f"  {c:>3}권: {s}")

print(f"\n--- 샘플 책 (처음 15권) ---")
for b in books_res.data[:15]:
    title = (b.get("title") or "?")[:35]
    genre = (b.get("genre") or "(NULL)")[:40]
    source = b.get("source") or "?"
    print(f"  {title:35s} | genre={genre:40s} | src={source}")
