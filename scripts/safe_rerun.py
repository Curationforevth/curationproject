"""안정적 re-run — 처리 완료 책 스킵, IO 분산, 진행률 실시간 출력

사용법:
  python3 scripts/safe_rerun.py           # 전체 (미처리분만)
  python3 scripts/safe_rerun.py 200       # 200권만
  python3 -u scripts/safe_rerun.py        # unbuffered stdout (백그라운드용)
"""
import os, sys, time

sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.reason_extractor import ReasonExtractor

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 99999
CHUNK = 20  # 한 번에 처리할 권수 (작게 → IO 분산)
SLEEP_BETWEEN_CHUNKS = 2  # chunk 사이 대기 (초)

def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

sb = make_client()

# 1) 전체 대상 수집
print(f"대상 도서 수집 (limit={LIMIT})...", flush=True)
all_ids = []
offset = 0
while len(all_ids) < LIMIT:
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
    all_ids.extend(r["id"] for r in res.data)
    if len(res.data) < 500:
        break
    offset += 500

all_ids = all_ids[:LIMIT]
print(f"  전체 {len(all_ids)}권", flush=True)

# 2) 이미 처리된 book_id 수집 → 스킵
print("이미 처리된 책 확인 중...", flush=True)
done_ids = set()
offset = 0
while True:
    for attempt in range(3):
        try:
            res = sb.table("book_love_reasons").select("book_id") \
                .range(offset, offset + 999).execute()
            break
        except Exception as e:
            print(f"  조회 재시도 {attempt+1}/3: {e}", flush=True)
            time.sleep(5)
            sb = make_client()
    else:
        break
    if not res.data:
        break
    done_ids.update(r["book_id"] for r in res.data)
    if len(res.data) < 1000:
        break
    offset += 1000

ids = [i for i in all_ids if i not in done_ids]
print(f"  이미 처리: {len(done_ids)}권 스킵, 남은 대상: {len(ids)}권\n", flush=True)

if not ids:
    print("모든 책이 이미 처리되었습니다.", flush=True)
    sys.exit(0)

# 2.5) 사전 테스트 — 1권으로 API 상태 확인
print("사전 테스트 (1권)...", flush=True)
test_id = ids[0]
try:
    test_res = sb.table("books") \
        .select("id, title, genre, description, rich_description, library_keywords") \
        .eq("id", test_id).execute()
    test_book = test_res.data[0] if test_res.data else None
    if test_book:
        test_ext = ReasonExtractor(sb, dry_run=False)
        test_ext._process_batch([test_book])
        # 성공하면 done_ids에 추가하여 메인 루프에서 스킵
        ids = [i for i in ids if i != test_id]
        print(f"  ✓ 사전 테스트 성공: {test_book.get('title', '?')[:30]}", flush=True)
        print(f"  남은 대상: {len(ids)}권\n", flush=True)
except Exception as e:
    error_msg = str(e)
    if "429" in error_msg:
        print(f"  ✗ Rate limit! Response: {error_msg[:200]}", flush=True)
    elif "401" in error_msg or "403" in error_msg:
        print(f"  ✗ 인증 실패! API 키 확인 필요: {error_msg[:200]}", flush=True)
    else:
        print(f"  ✗ 사전 테스트 실패: {error_msg[:200]}", flush=True)
    print("  배치를 시작하지 않습니다.", flush=True)
    sys.exit(1)

# 3) 처리 시작
start = time.time()
done, errors, consecutive_errors = 1, 0, 0  # 사전 테스트 1건 포함

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

    # 추출 + 저장 (기존 reason은 삭제하지 않음 — 신규만)
    extractor = ReasonExtractor(sb, dry_run=False)
    try:
        extractor._process_batch(books)
        done += len(books)
        consecutive_errors = 0
    except Exception as e:
        error_msg = str(e)
        consecutive_errors += 1
        errors += len(books)

        # rate limit vs quota vs 기타 구분
        if "429" in error_msg:
            print(f"  ✗ Rate limit (연속 {consecutive_errors}회): {error_msg[:150]}", flush=True)
            print(f"    → 60초 대기 후 재시도", flush=True)
            time.sleep(60)
        elif "401" in error_msg or "403" in error_msg:
            print(f"  ✗ 인증 에러 → 즉시 중단: {error_msg[:150]}", flush=True)
            break
        else:
            print(f"  ✗ 배치 실패 (연속 {consecutive_errors}회): {error_msg[:150]}", flush=True)
            time.sleep(5)
            sb = make_client()

        if consecutive_errors >= 3:
            print(f"\n연속 에러 3회 → 자동 중단", flush=True)
            print(f"  처리: {done}건, 에러: {errors}건", flush=True)
            break

    # 진행률
    elapsed = time.time() - start
    pct = (i + len(chunk_ids)) / len(ids) * 100
    rate = done / elapsed * 60 if elapsed > 0 else 0
    eta = (len(ids) - i - len(chunk_ids)) / rate if rate > 0 else 0
    print(f"  [{pct:5.1f}%] {done}/{len(ids)}완료 {errors}에러 "
          f"{elapsed/60:.1f}분경과 ~{eta:.0f}분남음", flush=True)

    # IO 분산을 위한 대기
    time.sleep(SLEEP_BETWEEN_CHUNKS)

elapsed = time.time() - start
print(f"\n{'='*50}", flush=True)
print(f"Re-run 완료: {done}권, {errors}에러, {elapsed/60:.1f}분", flush=True)
print(f"{'='*50}", flush=True)
