"""안정적 re-run — 타임아웃 감지, 자동 복구, 진행률 실시간 출력

사용법:
  python3 scripts/safe_rerun.py           # 전체 (기존 reason 있는 책 전부)
  python3 scripts/safe_rerun.py 200       # 200권만
  python3 -u scripts/safe_rerun.py 500    # 500권, unbuffered stdout (백그라운드용)
"""
import os, sys, time

sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.reason_extractor import ReasonExtractor

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 99999
CHUNK = 40  # 한 번에 상세조회+처리하는 권수 (BATCH_SIZE 20 × 2배치)

def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

sb = make_client()

# 대상 수집 (books 테이블, 가벼움)
print(f"대상 도서 수집 (limit={LIMIT})...", flush=True)
ids = []
offset = 0
while len(ids) < LIMIT:
    for attempt in range(3):
        try:
            res = sb.table("books").select("id").not_.is_("rich_description", "null") \
                .range(offset, offset + 499).execute()
            break
        except Exception as e:
            print(f"  수집 재시도 {attempt+1}/3: {e}", flush=True)
            time.sleep(5)
            sb = make_client()
    else:
        print("  수집 실패, 현재까지로 진행", flush=True)
        break
    if not res.data:
        break
    ids.extend(r["id"] for r in res.data)
    if len(res.data) < 500:
        break
    offset += 500

ids = ids[:LIMIT]
print(f"  {len(ids)}권 대상\n", flush=True)

start = time.time()
done, errors = 0, 0

for i in range(0, len(ids), CHUNK):
    chunk_ids = ids[i:i + CHUNK]

    # 상세 조회
    for attempt in range(3):
        try:
            res = sb.table("books") \
                .select("id, title, genre, description, rich_description, library_keywords") \
                .in_("id", chunk_ids).execute()
            break
        except Exception as e:
            print(f"  상세조회 재시도 {attempt+1}/3: {e}", flush=True)
            time.sleep(5)
            sb = make_client()
    else:
        errors += len(chunk_ids)
        continue

    books = res.data or []
    if not books:
        continue

    # 기존 reason 삭제 — IN 배치
    for attempt in range(3):
        try:
            sb.table("book_love_reasons").delete().in_("book_id", chunk_ids).execute()
            break
        except Exception:
            time.sleep(3)
            sb = make_client()

    # 추출 + 저장
    extractor = ReasonExtractor(sb, dry_run=False)
    try:
        extractor._process_batch(books)
        done += len(books)
    except Exception as e:
        print(f"  ✗ 배치 실패: {e}", flush=True)
        errors += len(books)
        time.sleep(5)
        sb = make_client()

    # 진행률
    elapsed = time.time() - start
    pct = (i + len(chunk_ids)) / len(ids) * 100
    rate = done / elapsed * 60 if elapsed > 0 else 0
    eta = (len(ids) - i - len(chunk_ids)) / rate if rate > 0 else 0
    print(f"  [{pct:5.1f}%] {done}/{len(ids)}완료 {errors}에러 "
          f"{elapsed/60:.1f}분경과 ~{eta:.0f}분남음", flush=True)
    time.sleep(0.5)

elapsed = time.time() - start
print(f"\n{'='*50}", flush=True)
print(f"Re-run 완료: {done}권, {errors}에러, {elapsed/60:.1f}분", flush=True)
print(f"{'='*50}", flush=True)
