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


print("=== v3 데이터 검증 ===\n")

# 1. 커버리지
books_with_rich = sb.table("books").select("id", count="exact") \
    .not_.is_("rich_description", "null").limit(0).execute().count
v3_count = sb.table("book_v3_vectors").select("book_id", count="exact").limit(0).execute().count
check("커버리지", v3_count >= books_with_rich * 0.99,
      f"book_v3_vectors={v3_count}, books(rich)={books_with_rich}")

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

# 4. reason 커버리지
reason_books = set()
offset = 0
while True:
    res = sb.table("book_love_reasons").select("book_id") \
        .eq("source", "llm_extracted").range(offset, offset + 999).execute()
    if not res.data:
        break
    reason_books.update(r["book_id"] for r in res.data)
    if len(res.data) < 1000:
        break
    offset += 1000
check("reason 커버리지", len(reason_books) >= 2400,
      f"distinct book_id={len(reason_books)}")

# 5. reason 품질 (5개 미만)
reason_counts = Counter()
offset = 0
while True:
    res = sb.table("book_love_reasons").select("book_id") \
        .eq("source", "llm_extracted").range(offset, offset + 999).execute()
    if not res.data:
        break
    for r in res.data:
        reason_counts[r["book_id"]] += 1
    if len(res.data) < 1000:
        break
    offset += 1000
under_5 = sum(1 for c in reason_counts.values() if c < 5)
check("reason 품질", under_5 <= 80, f"5개 미만: {under_5}권")

# 6. 서버 로딩 JOIN 시뮬레이션
print("\n서버 로딩 JOIN 샘플:")
sample = sb.table("book_v3_vectors") \
    .select("book_id, l1_text, l2_text, l1_genre_id, l2_genre_id") \
    .limit(5).execute().data
for row in sample:
    l1_ok = "✓" if row.get("l1_genre_id") else "NULL"
    l2_ok = "✓" if row.get("l2_genre_id") else "NULL"
    print(f"  {row['book_id'][:8]}... L1={l1_ok} L2={l2_ok} "
          f"({row.get('l1_text', '?')}>{(row.get('l2_text') or '?')[:20]})")

print(f"\n{'='*50}")
print(f"결과: {passed} 통과, {failed} 실패")
if failed:
    print("⚠️  실패한 항목을 확인하세요.")
else:
    print("✓ 모든 검증 통과")
