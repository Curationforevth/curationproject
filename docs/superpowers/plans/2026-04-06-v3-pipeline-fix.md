# v3 파이프라인 수정 + 배치 실행 + YES24 진단 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v3 데이터 생성 스크립트 4개의 안정성 버그를 수정하고, 배치를 실행하여 추천 서버 인덱스를 재빌드한다. 추가로 YES24 매칭 실패 원인 진단 스크립트를 작성한다.

**Architecture:** 기존 스크립트 4개(`generate_genre_embeddings.py`, `generate_book_v3_vectors.py`, `v3_reason_extract.py`, `verify_v3_data.py`)의 인플레이스 수정. 공통 체크포인트 로직은 각 스크립트에 직접 추가 (별도 모듈 불필요). YES24 진단은 기존 스크래퍼 로직을 재사용하는 독립 스크립트.

**Tech Stack:** Python 3.9+, Supabase, OpenAI API (text-embedding-3-large, gpt-4o-mini), requests, BeautifulSoup

**Spec:** `docs/superpowers/specs/2026-04-06-v3-pipeline-fix-and-enrichment-design.md`

---

### Task 1: generate_genre_embeddings.py 수정

**Files:**
- Modify: `scripts/generate_genre_embeddings.py`

- [ ] **Step 1: dimension 하드코딩 → 상수 사용**

Line 15에 이미 `from scripts.lib.openai_helpers import call_embedding`이 있으므로 `EMBEDDING_DIMENSIONS`도 임포트.

```python
# line 15 변경
from scripts.lib.openai_helpers import call_embedding, EMBEDDING_DIMENSIONS
```

Line 98의 하드코딩된 2000을 상수로 교체:

```python
# line 98 변경
        assert len(test_emb) == 1 and len(test_emb[0]) == EMBEDDING_DIMENSIONS
```

- [ ] **Step 2: batch INSERT 실패 시 개별 재시도 fallback**

Lines 134-144를 수정. 현재는 batch 실패 시 통째로 스킵되는데, 1건씩 재시도 추가:

```python
        # line 134-144 교체
        try:
            sb.table("genre_embeddings").insert(rows).execute()
            done += len(rows)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 INSERT 실패, 1건씩 재시도: {e}", flush=True)
            for row in rows:
                try:
                    sb.table("genre_embeddings").insert(row).execute()
                    done += 1
                except Exception as e2:
                    errors += 1
                    print(f"    ✗ [{row['level']}] {row['genre_text'][:30]}: {e2}", flush=True)
            consecutive_errors = 0  # 개별 재시도했으므로 리셋
```

- [ ] **Step 3: 변경 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 scripts/generate_genre_embeddings.py --dry-run`

Expected: 정상 실행, 장르 목록 출력 (API 호출 없음)

- [ ] **Step 4: 커밋**

```bash
git add scripts/generate_genre_embeddings.py
git commit -m "fix: genre_embeddings — dimension 상수화, INSERT fallback 추가"
```

---

### Task 2: generate_book_v3_vectors.py 수정

**Files:**
- Modify: `scripts/generate_book_v3_vectors.py`

- [ ] **Step 1: dimension 하드코딩 → 상수 사용**

```python
# line 18 변경
from scripts.lib.openai_helpers import call_embedding, EMBEDDING_DIMENSIONS
```

```python
# line 154 변경
        assert len(test_emb) == 1 and len(test_emb[0]) == EMBEDDING_DIMENSIONS
```

- [ ] **Step 2: pagination 수정 (500 → 1000)**

Line 83의 `fetch_target_books` 함수:

```python
    # line 79-90 교체 (while 루프 전체)
    while len(books) < limit:
        res = sb.table("books") \
            .select("id, title, genre, description, rich_description") \
            .not_.is_("rich_description", "null") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        books.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return books[:limit]
```

- [ ] **Step 3: 체크포인트 상태 파일 추가**

스크립트 상단에 import와 경로 상수 추가:

```python
# line 11 뒤에 추가
import json as _json

CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), ".checkpoint_book_v3.json")
```

체크포인트 저장/로드 함수 추가 (main() 전):

```python
def load_checkpoint():
    """체크포인트 파일에서 완료된 book_id 목록 로드."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            data = _json.load(f)
            print(f"  체크포인트 로드: {len(data.get('done_ids', []))}건", flush=True)
            return set(data.get("done_ids", []))
    return set()


def save_checkpoint(done_ids):
    """처리 완료된 book_id를 체크포인트 파일에 저장."""
    with open(CHECKPOINT_FILE, "w") as f:
        _json.dump({"done_ids": list(done_ids), "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
```

main() 내에서 `existing` 변수에 체크포인트 합산 (line 112 근처):

```python
    # line 111-113 교체
    all_books = fetch_target_books(sb, args.limit)
    existing = get_existing_book_ids(sb)
    checkpoint_ids = load_checkpoint()
    existing = existing | checkpoint_ids
    books = [b for b in all_books if b["id"] not in existing]
```

체크포인트 로그 (line 214-215) 를 실제 저장으로 교체:

```python
        # line 214-215 교체
        if done > 0 and done % CHECKPOINT_INTERVAL == 0:
            all_done = existing | {p["book_id"] for p in prepared[:i + len(batch)] if p["book_id"] not in existing}
            save_checkpoint(all_done)
            print(f"  ── 체크포인트: {done}건 완료, 상태 저장 ──", flush=True)
```

배치 완료 후 체크포인트 파일 삭제 (최종 리포트 뒤):

```python
    # line 222 뒤 추가
    if errors == 0 and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("  체크포인트 파일 삭제 (정상 완료)", flush=True)
```

- [ ] **Step 4: embedding API 실패 시 개별 재시도**

Lines 168-179 교체:

```python
        # line 168-179 교체 — embedding 실패 시 개별 재시도
        try:
            embeddings = call_embedding(texts)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 임베딩 실패, 1건씩 재시도: {e}", flush=True)
            embeddings = []
            for t in texts:
                try:
                    emb = call_embedding([t])
                    embeddings.append(emb[0])
                except Exception as e2:
                    embeddings.append(None)
                    errors += 1
                    print(f"    ✗ 개별 임베딩 실패: {e2}", flush=True)
                time.sleep(SLEEP_BETWEEN)
            consecutive_errors = 0
```

rows 생성 부분도 None 임베딩 처리 추가 (lines 181-191):

```python
        # line 181-191 교체
        rows = []
        for p, emb in zip(batch, embeddings):
            if emb is None:
                continue  # 임베딩 실패한 건 스킵
            rows.append({
                "book_id": p["book_id"],
                "desc_embedding": emb,
                "source_text": p["source_text"],
                "l1_text": p["l1_text"],
                "l2_text": p["l2_text"],
                "l1_genre_id": p["l1_genre_id"],
                "l2_genre_id": p["l2_genre_id"],
            })

        if not rows:
            time.sleep(SLEEP_BETWEEN)
            continue
```

- [ ] **Step 5: 변경 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 scripts/generate_book_v3_vectors.py --dry-run`

Expected: 정상 실행, 대상 도서 목록 출력

- [ ] **Step 6: 커밋**

```bash
git add scripts/generate_book_v3_vectors.py
git commit -m "fix: book_v3_vectors — pagination, checkpoint, embedding fallback"
```

---

### Task 3: v3_reason_extract.py 수정

**Files:**
- Modify: `scripts/v3_reason_extract.py`

- [ ] **Step 1: import 경로 수정**

Line 30-31 변경. 다른 스크립트들은 `scripts.lib.` 패턴을 사용:

```python
# line 30-31 교체
from scripts.lib.openai_helpers import call_chat, call_embedding
from scripts.lib.retry import with_retry
```

- [ ] **Step 2: pagination 수정 (500 → 1000)**

Line 326-328의 books 조회:

```python
# line 328 변경
                .range(offset, offset + 999).execute()
```

Line 339-340의 break 조건:

```python
# line 339 변경
        if len(res.data) < 1000:
```

Line 341 변경:

```python
# line 341 변경
        offset += 1000
```

- [ ] **Step 3: ThreadPoolExecutor 타임아웃 추가**

Line 417 변경:

```python
# line 417 변경
                    reasons = future.result(timeout=60)
```

- [ ] **Step 4: 임베딩 실패 로깅 강화**

Line 159 뒤에 실패 건수 로깅 추가. 현재 `embed_and_save` 내 lines 161-163:

```python
    # line 161-166 교체
    valid = [(all_reasons[i], all_embeddings[i], reason_map[i])
             for i in range(len(all_reasons)) if all_embeddings[i] is not None]

    skipped = len(all_reasons) - len(valid)
    if skipped > 0:
        print(f"  ⚠ 임베딩 실패로 {skipped}/{len(all_reasons)}건 스킵", flush=True)

    if not valid:
        return 0, len(all_reasons)
```

- [ ] **Step 5: INSERT fallback 5-row → 1-row**

Lines 184-193 교체:

```python
        except Exception:
            # 배치 실패 → 1건씩 재시도
            for row in chunk:
                try:
                    with_retry(lambda r=row: sb.table("book_love_reasons").insert(r).execute(),
                               max_retries=2, base_delay=1.0)
                    saved += 1
                except Exception as e:
                    print(f"  ✗ insert 실패: {e}", flush=True)
                    failed += 1
            time.sleep(1)
```

- [ ] **Step 6: 체크포인트 상태 파일 추가**

상단에 import 추가 (line 5 뒤):

```python
import json as _json
```

상수 추가 (line 41 뒤):

```python
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), ".checkpoint_v3_reason.json")
```

체크포인트 저장/로드 함수 추가 (main() 전, `run_checkpoint` 뒤):

```python
def load_reason_checkpoint():
    """체크포인트 파일에서 완료된 book_id 목록 로드."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            data = _json.load(f)
            print(f"  체크포인트 로드: {len(data.get('done_ids', []))}건", flush=True)
            return set(data.get("done_ids", []))
    return set()


def save_reason_checkpoint(done_ids):
    """처리 완료된 book_id를 체크포인트 파일에 저장."""
    with open(CHECKPOINT_FILE, "w") as f:
        _json.dump({"done_ids": list(done_ids), "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
```

main() 내에서 done_ids에 체크포인트 합산 (line 369 근처):

```python
    # line 369 앞에 추가
    checkpoint_ids = load_reason_checkpoint()
    done_ids = done_ids | checkpoint_ids
```

- [ ] **Step 7: 체크포인트 조건 단순화 + 상태 저장**

Line 456 교체:

```python
        # line 456-462 교체
        if do_checkpoint and total_done > 0 and total_done % CHECKPOINT_INTERVAL == 0:
            checkpoint_num += 1
            # 상태 파일 저장
            processed_so_far = set(ids[:i + len(chunk_ids)])
            save_reason_checkpoint(done_ids | processed_so_far)
            passed = run_checkpoint(sb, checkpoint_num, total_done, total_saved, total_errors)
            if not passed:
                print("⛔ 품질 검증 실패 — 자동 중단. 위 이슈를 확인하세요.", flush=True)
                break
            print(">> 품질 검증 통과. 계속 진행합니다...\n", flush=True)
```

배치 완료 후 체크포인트 삭제 (최종 리포트 뒤, line 476 뒤):

```python
    if total_errors == 0 and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("  체크포인트 파일 삭제 (정상 완료)", flush=True)
```

- [ ] **Step 8: 중복 Counter import 정리**

Line 21의 `from collections import Counter`와 line 261의 `from collections import Counter as _Counter`가 중복. Line 261 삭제하고 line 262에서 `_Counter` → `Counter` 변경:

```python
# line 261 삭제
# line 262 변경
            dupes = [r for r, c in Counter(reasons).items() if c >= 3]
```

- [ ] **Step 9: 변경 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 -c "from scripts.v3_reason_extract import build_v3_prompt, filter_v3_reasons; print('import OK')"`

Expected: `import OK` (임포트 경로 문제 없음 확인)

- [ ] **Step 10: 커밋**

```bash
git add scripts/v3_reason_extract.py
git commit -m "fix: v3_reason_extract — import, pagination, timeout, checkpoint, INSERT fallback"
```

---

### Task 4: verify_v3_data.py 수정

**Files:**
- Modify: `scripts/verify_v3_data.py`

- [ ] **Step 1: source 필터 수정 + 에러 핸들링 + FK 검증 + dimension 검증**

전체 파일 교체 (구조 유지하면서 버그 수정):

```python
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
```

- [ ] **Step 2: 변경 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 -c "import scripts.verify_v3_data" 2>&1 | head -5`

Expected: 스크립트가 실행되며 검증 시작 (현재 데이터 기준 일부 FAIL 예상 — 아직 v3 배치 안 돌렸으므로)

- [ ] **Step 3: 커밋**

```bash
git add scripts/verify_v3_data.py
git commit -m "fix: verify_v3_data — source 필터, FK 검증, dimension 검증, 에러 핸들링"
```

---

### Task 5: YES24 매칭 진단 스크립트 작성

**Files:**
- Create: `scripts/yes24_match_diagnostic.py`

- [ ] **Step 1: 진단 스크립트 작성**

```python
# scripts/yes24_match_diagnostic.py
"""YES24 매칭 실패 원인 진단.

rich_description이 NULL인 책 300권을 샘플링하여
YES24 매칭 실패 원인을 분류하고 통계를 출력한다.

사용법:
  python3 scripts/yes24_match_diagnostic.py              # 300권 진단
  python3 scripts/yes24_match_diagnostic.py --limit 50   # 50권만
"""
import argparse
import csv
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.yes24_scraper import (
    is_non_standard_isbn, build_search_query,
    extract_isbn_from_html, isbn_matches, clean_section_text,
)

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("requests, beautifulsoup4 필요: pip install requests beautifulsoup4")
    sys.exit(1)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36',
}
REQUEST_DELAY = 1.0
MAX_SEARCH_RESULTS = 5
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "yes24_diagnostic_result.csv")


def fetch_sample_books(sb, limit, non_standard_count=50):
    """stratified 샘플링: 비표준 ISBN + 일반 ISBN."""
    all_books = []
    offset = 0
    while True:
        res = sb.table("books") \
            .select("id, isbn, title, author") \
            .is_("rich_description", "null") \
            .not_.is_("isbn", "null") \
            .order("sales_point", desc=True) \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        all_books.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    non_standard = [b for b in all_books if is_non_standard_isbn(b["isbn"])]
    standard = [b for b in all_books if not is_non_standard_isbn(b["isbn"])]

    # stratified: 비표준 최대 non_standard_count + 나머지 일반
    sample_ns = non_standard[:min(non_standard_count, len(non_standard))]
    remaining = limit - len(sample_ns)
    sample_std = random.sample(standard, min(remaining, len(standard))) if standard else []

    print(f"  전체 NULL: {len(all_books)}권 (비표준: {len(non_standard)}, 일반: {len(standard)})")
    print(f"  샘플: 비표준 {len(sample_ns)} + 일반 {len(sample_std)} = {len(sample_ns) + len(sample_std)}권")

    return sample_ns + sample_std


def diagnose_book(session, book):
    """단일 책의 YES24 매칭 시도 + 실패 원인 분류."""
    isbn = book["isbn"]
    title = book["title"]
    author = book.get("author", "") or ""

    # 1) 비표준 ISBN
    if is_non_standard_isbn(isbn):
        return "non_standard_isbn", None

    # 2) YES24 검색
    query = build_search_query(title, author)
    try:
        url = f'https://www.yes24.com/Product/Search?domain=BOOK&query={requests.utils.quote(query)}'
        r = session.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        goods_ids = [el.get('data-goods-no') for el in soup.select('[data-goods-no]')[:MAX_SEARCH_RESULTS]]
    except Exception as e:
        return "search_error", str(e)

    if not goods_ids:
        return "not_found", f"query={query}"

    # 3) 상세 페이지 ISBN 매칭
    for i, goods_id in enumerate(goods_ids):
        try:
            r = session.get(f'https://www.yes24.com/Product/Goods/{goods_id}', timeout=10)
            r.raise_for_status()
            html = r.text
        except Exception:
            continue

        page_isbn = extract_isbn_from_html(html)
        if isbn_matches(page_isbn, isbn):
            # 4) 콘텐츠 추출 시도
            soup2 = BeautifulSoup(html, 'html.parser')
            has_content = False
            for sid in ['infoset_introduce', 'infoset_pubReivew', 'infoset_inBook']:
                el = soup2.select_one(f'#{sid}')
                if el:
                    raw = el.get_text(separator='\n', strip=True)
                    cleaned = clean_section_text(raw)
                    if cleaned:
                        has_content = True
                        break
            if has_content:
                return "success", f"goods_id={goods_id}"
            else:
                return "no_content", f"goods_id={goods_id}"

        time.sleep(0.3)

    return "isbn_mismatch", f"goods_ids={goods_ids}, db_isbn={isbn}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    print(f"YES24 매칭 진단 (샘플 {args.limit}권)\n")
    books = fetch_sample_books(sb, args.limit)

    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    for i, book in enumerate(books):
        reason, detail = diagnose_book(session, book)
        results.append({
            "isbn": book["isbn"],
            "title": book["title"][:40],
            "author": (book.get("author") or "")[:20],
            "result": reason,
            "detail": detail or "",
        })

        if (i + 1) % 50 == 0 or i + 1 == len(books):
            print(f"  [{i+1}/{len(books)}] 진행 중...", flush=True)

        time.sleep(REQUEST_DELAY)

    # CSV 저장
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["isbn", "title", "author", "result", "detail"])
        writer.writeheader()
        writer.writerows(results)
    print(f"\n결과 CSV: {OUTPUT_CSV}")

    # 통계 출력
    from collections import Counter
    counts = Counter(r["result"] for r in results)
    total = len(results)

    print(f"\n{'='*50}")
    print(f"YES24 매칭 진단 결과 ({total}권)")
    print(f"{'='*50}")
    for reason in ["success", "not_found", "isbn_mismatch", "non_standard_isbn", "no_content", "search_error"]:
        cnt = counts.get(reason, 0)
        pct = cnt / total * 100 if total else 0
        marker = "⚠" if pct >= 20 else " "
        print(f"  {marker} {reason:20s}: {cnt:4d}건 ({pct:5.1f}%)")
    print(f"{'='*50}")

    # 판단 기준 안내
    print("\n판단 기준:")
    if counts.get("isbn_mismatch", 0) / total >= 0.20:
        print("  → isbn_mismatch ≥ 20%: ISBN 양방향 변환 + fuzzy title 매칭 추가 권장")
    if counts.get("not_found", 0) / total >= 0.20:
        print("  → not_found ≥ 20%: 검색 쿼리 변형 로직 추가 권장")
    if counts.get("non_standard_isbn", 0) / total >= 0.10:
        print("  → non_standard_isbn ≥ 10%: title-only 검색 모드 추가 권장")
    if counts.get("no_content", 0) / total >= 0.10:
        print("  → no_content ≥ 10%: 추가 HTML 섹션 탐색 권장")
    if counts.get("success", 0) / total >= 0.15:
        print("  → success ≥ 15%: 단순 재실행만으로 커버리지 향상 가능")
    if all(counts.get(r, 0) / total < 0.10 for r in ["isbn_mismatch", "not_found", "non_standard_isbn", "no_content"]):
        print("  → 모든 유형 < 10%: 현상 유지 (개선 ROI 낮음)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 구문 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 -c "import ast; ast.parse(open('scripts/yes24_match_diagnostic.py').read()); print('syntax OK')"`

Expected: `syntax OK`

- [ ] **Step 3: 커밋**

```bash
git add scripts/yes24_match_diagnostic.py
git commit -m "feat: YES24 매칭 실패 진단 스크립트"
```

---

### Task 6: 배치 실행 — genre_embeddings

**Files:** (실행만, 코드 변경 없음)

- [ ] **Step 1: .env 확인**

Run: `cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 -c "from dotenv import load_dotenv; load_dotenv('.env'); import os; print('OPENAI:', 'OK' if os.getenv('OPENAI_API_KEY') else 'MISSING'); print('SUPA:', 'OK' if os.getenv('SUPABASE_URL') else 'MISSING')"`

Expected: 둘 다 OK

- [ ] **Step 2: genre_embeddings dry-run**

Run: `python3 scripts/generate_genre_embeddings.py --dry-run`

Expected: 대상 장르 목록 출력, 건수 확인

- [ ] **Step 3: genre_embeddings 실행**

Run: `python3 scripts/generate_genre_embeddings.py`

Expected: ~320건 처리, 에러 0, ~10분 이내 완료

- [ ] **Step 4: 결과 확인**

완료 메시지에서 `저장`, `에러` 건수 확인. 에러 > 0이면 중단하고 원인 파악.

---

### Task 7: 배치 실행 — book_v3_vectors

- [ ] **Step 1: book_v3_vectors dry-run**

Run: `python3 scripts/generate_book_v3_vectors.py --dry-run`

Expected: 대상 도서 수 출력 (~2,510권), 처음 3건 샘플

- [ ] **Step 2: book_v3_vectors 실행**

Run: `python3 scripts/generate_book_v3_vectors.py`

Expected: ~2,510건 처리, 100건마다 체크포인트 저장, ~1시간

- [ ] **Step 3: 결과 확인**

완료 메시지에서 건수/에러 확인. 체크포인트 파일이 삭제되었는지 확인 (정상 완료 시).

---

### Task 8: 배치 실행 — v3_reason_extract (Task 7과 병렬 가능)

- [ ] **Step 1: v3_reason_extract 실행**

Run: `python3 -u scripts/v3_reason_extract.py --limit 20`

Expected: 20권 처리, 품질 체크포인트 출력, 정상 완료. reason 샘플이 15-40자 맥락 보존 명사구인지 확인.

- [ ] **Step 2: 소규모 테스트 결과 확인 후 전체 실행**

20권 결과가 정상이면:

Run: `python3 -u scripts/v3_reason_extract.py`

Expected: ~757권 처리, 100권마다 품질 체크포인트, ~30분

---

### Task 9: 검증 + 인덱스 재빌드

- [ ] **Step 1: verify_v3_data 실행**

Run: `python3 scripts/verify_v3_data.py`

Expected: 모든 항목 ✓ 통과. 실패 항목이 있으면 해당 스크립트 재실행.

- [ ] **Step 2: 인덱스 재빌드**

Run: `python3 scripts/build_index.py`

Expected: index.pkl 생성 (기존 ~170MB보다 클 수 있음)

- [ ] **Step 3: 커밋 + 푸시**

```bash
git add -A  # index.pkl은 Git LFS 추적
git commit -m "chore: v3 데이터 생성 완료 + 인덱스 재빌드"
```

Render 서버 자동 재배포 확인 (push → build → deploy).

---

### Task 10: YES24 진단 실행

- [ ] **Step 1: 진단 스크립트 실행**

Run: `python3 scripts/yes24_match_diagnostic.py --limit 300`

Expected: ~5분 소요, CSV + 통계 출력. 판단 기준에 따라 다음 액션 결정.

- [ ] **Step 2: Eden에게 결과 공유**

통계 결과와 판단 기준 매칭 결과를 Eden에게 보고. 개선 방향 결정.
