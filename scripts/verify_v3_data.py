# scripts/verify_v3_data.py
"""v3 데이터 생성 결과 검증. 스펙 섹션 7 체크리스트."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from collections import Counter

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

passed, failed = 0, 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} — {detail}")


def paginate_select(table, select_cols, filters=None):
    """pagination 헬퍼. filters: [(method, args)] 리스트."""
    all_data = []
    offset = 0
    while True:
        try:
            q = sb.table(table).select(select_cols)
            for method, args in (filters or []):
                q = getattr(q, method)(*args) if isinstance(args, (list, tuple)) else getattr(q, method)(args)
            res = q.range(offset, offset + 999).execute()
        except Exception as e:
            print(f"  ⚠ {table} 조회 실패 (offset={offset}): {e}")
            break
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return all_data


print("=== v3 데이터 검증 ===\n")

# 1. 커버리지
books_with_rich = sb.table("books").select("id", count="exact") \
    .not_.is_("rich_description", "null").limit(0).execute().count
v3_count = sb.table("book_v3_vectors").select("book_id", count="exact").limit(0).execute().count
check("커버리지", v3_count >= books_with_rich * 0.995,
      f"book_v3_vectors={v3_count}, books(rich)={books_with_rich} ({v3_count/books_with_rich*100:.1f}%)" if books_with_rich else "books=0")

# 2. NULL desc 체크
null_desc = sb.table("book_v3_vectors").select("book_id", count="exact") \
    .is_("desc_embedding", "null").limit(0).execute().count
check("desc NULL 없음", null_desc == 0, f"NULL desc: {null_desc}건")

# 3. genre_embeddings 분포
ge_l1 = sb.table("genre_embeddings").select("id", count="exact") \
    .eq("level", "l1").limit(0).execute().count
ge_l2 = sb.table("genre_embeddings").select("id", count="exact") \
    .eq("level", "l2").limit(0).execute().count
check("L1 분포", 15 <= ge_l1 <= 30, f"L1={ge_l1}")
check("L2 분포", ge_l2 >= 100, f"L2={ge_l2}")

# 4. reason 커버리지 (llm_extracted + v3_context_rich 모두 포함)
reason_data = paginate_select("book_love_reasons", "book_id, source")
reason_books = set(r["book_id"] for r in reason_data)
v3_reason_books = set(r["book_id"] for r in reason_data if r.get("source") == "v3_context_rich")
check("reason 커버리지 (전체)", len(reason_books) >= 2400,
      f"distinct book_id={len(reason_books)}")
print(f"    (llm_extracted + v3_context_rich 합산, v3만: {len(v3_reason_books)}권)")

# 5. reason 품질 (5개 미만)
reason_counts = Counter(r["book_id"] for r in reason_data)
under_5 = sum(1 for c in reason_counts.values() if c < 5)
check("reason 품질", under_5 <= 80, f"5개 미만: {under_5}권")

# 6. FK 검증 — l1/l2_genre_id가 genre_embeddings에 실제 존재하는지
genre_ids_data = paginate_select("genre_embeddings", "id")
valid_genre_ids = set(r["id"] for r in genre_ids_data)

v3_fk_data = paginate_select("book_v3_vectors", "book_id, l1_genre_id, l2_genre_id")
orphan_l1 = sum(1 for r in v3_fk_data if r.get("l1_genre_id") and r["l1_genre_id"] not in valid_genre_ids)
orphan_l2 = sum(1 for r in v3_fk_data if r.get("l2_genre_id") and r["l2_genre_id"] not in valid_genre_ids)
null_l1 = sum(1 for r in v3_fk_data if not r.get("l1_genre_id"))
null_l2 = sum(1 for r in v3_fk_data if not r.get("l2_genre_id"))
check("FK 무결성 (L1)", orphan_l1 == 0, f"고아 FK: {orphan_l1}건")
check("FK 무결성 (L2)", orphan_l2 == 0, f"고아 FK: {orphan_l2}건")
print(f"    (NULL L1: {null_l1}건, NULL L2: {null_l2}건 — 장르 미분류 책)")

# 7. 벡터 dimension 검증 (샘플 5건)
dim_sample = sb.table("book_v3_vectors").select("book_id, desc_embedding").limit(5).execute().data
if dim_sample:
    dims = [len(r["desc_embedding"]) for r in dim_sample if r.get("desc_embedding")]
    all_correct = all(d == 2000 for d in dims)
    check("벡터 dimension (2000D)", all_correct, f"dimensions: {dims}")
else:
    check("벡터 dimension (2000D)", False, "샘플 없음")

# 8. 서버 로딩 JOIN 시뮬레이션
print("\n서버 로딩 JOIN 샘플:")
sample = sb.table("book_v3_vectors") \
    .select("book_id, l1_text, l2_text, l1_genre_id, l2_genre_id") \
    .limit(5).execute().data
for row in (sample or []):
    l1_ok = "✓" if row.get("l1_genre_id") else "NULL"
    l2_ok = "✓" if row.get("l2_genre_id") else "NULL"
    print(f"  {row['book_id'][:8]}... L1={l1_ok} L2={l2_ok} "
          f"({row.get('l1_text', '?')}>{(row.get('l2_text') or '?')[:20]})")

print(f"\n{'='*50}")
print(f"결과: {passed} 통과, {failed} 실패")
if failed:
    print("⚠ 실패한 항목을 확인하세요.")
else:
    print("✓ 모든 검증 통과")
