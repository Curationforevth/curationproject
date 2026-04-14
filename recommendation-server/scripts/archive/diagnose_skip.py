#!/usr/bin/env python3
"""build_index skip 원인 진단. DB에서 직접 확인."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# 1. 각 테이블 row 수
books = sb.table("books").select("id", count="exact").execute()
v3 = sb.table("book_v3_vectors").select("book_id", count="exact").execute()
genres = sb.table("genre_embeddings").select("id", count="exact").execute()
reasons = sb.table("book_love_reasons").select("book_id", count="exact").execute()

print(f"books: {books.count}")
print(f"book_v3_vectors: {v3.count}")
print(f"genre_embeddings: {genres.count}")
print(f"book_love_reasons: {reasons.count}")

# 2. v3 book_ids 샘플
print("\n--- v3 book_ids 샘플 ---")
v3_sample = sb.table("book_v3_vectors").select("book_id,l1_genre_id,l2_genre_id,desc_embedding").limit(5).execute()
for r in v3_sample.data:
    has_desc = r.get("desc_embedding") is not None
    print(f"  {r['book_id'][:8]}... l1={r.get('l1_genre_id')}, l2={r.get('l2_genre_id')}, has_desc={has_desc}")

# 3. v3에서 l1_genre_id 또는 l2_genre_id가 NULL인 것
v3_no_genre = sb.table("book_v3_vectors").select("book_id", count="exact").or_("l1_genre_id.is.null,l2_genre_id.is.null").execute()
print(f"\nv3 with NULL l1/l2: {v3_no_genre.count}")

# 4. v3에서 desc_embedding이 NULL인 것
v3_no_desc = sb.table("book_v3_vectors").select("book_id", count="exact").is_("desc_embedding", "null").execute()
print(f"v3 with NULL desc_embedding: {v3_no_desc.count}")

# 5. genre_embeddings의 ID 목록
genre_ids = sb.table("genre_embeddings").select("id").execute()
genre_id_set = set(g["id"] for g in genre_ids.data)
print(f"\ngenre_embeddings IDs ({len(genre_id_set)}): {sorted(genre_id_set)[:10]}...")

# 6. v3의 l1_genre_id/l2_genre_id 유니크 값
v3_l1 = sb.table("book_v3_vectors").select("l1_genre_id").not_.is_("l1_genre_id", "null").limit(1000).execute()
v3_l2 = sb.table("book_v3_vectors").select("l2_genre_id").not_.is_("l2_genre_id", "null").limit(1000).execute()
l1_ids = set(r["l1_genre_id"] for r in v3_l1.data)
l2_ids = set(r["l2_genre_id"] for r in v3_l2.data)
missing_l1 = l1_ids - genre_id_set
missing_l2 = l2_ids - genre_id_set
print(f"v3 unique l1_genre_ids: {len(l1_ids)}")
print(f"v3 unique l2_genre_ids: {len(l2_ids)}")
print(f"l1 IDs missing from genre_embeddings: {len(missing_l1)}")
if missing_l1:
    print(f"  examples: {list(missing_l1)[:5]}")
print(f"l2 IDs missing from genre_embeddings: {len(missing_l2)}")
if missing_l2:
    print(f"  examples: {list(missing_l2)[:5]}")
