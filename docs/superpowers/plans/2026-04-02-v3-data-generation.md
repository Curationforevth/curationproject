# v3 미처리 데이터 생성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v3 추천 엔진에 필요한 desc/L1/L2 임베딩을 생성하고, 미처리 757권의 reason을 추출한다.

**Architecture:** Supabase에 2개 신규 테이블(genre_embeddings, book_v3_vectors) 생성 후, 배치 스크립트로 OpenAI 임베딩 API를 호출하여 데이터를 채운다. 장르 파싱 로직은 공유 모듈로 분리하여 일관성 보장.

**Tech Stack:** Python 3, Supabase (PostgreSQL + pgvector), OpenAI text-embedding-3-large (2000D), scripts/lib/openai_helpers.py

**Spec:** `docs/superpowers/specs/2026-04-02-v3-data-generation-design.md`

---

## File Structure

```
scripts/
├── lib/
│   ├── openai_helpers.py          # 기존 (변경 없음)
│   └── genre_parser.py            # 신규: L1/L2 파싱 + clean_html
├── generate_genre_embeddings.py   # 신규: genre_embeddings 테이블 채우기
├── generate_book_v3_vectors.py    # 신규: book_v3_vectors 테이블 채우기
├── verify_v3_data.py              # 신규: 검증 스크립트
├── safe_rerun.py                  # 수정: 연속 에러 중단 로직 추가
├── sql/
│   └── create_v3_tables.sql       # 신규: DDL
└── tests/
    └── test_genre_parser.py       # 신규: 파싱 유닛 테스트
```

---

### Task 1: DB 테이블 생성

**Files:**
- Create: `scripts/sql/create_v3_tables.sql`

- [ ] **Step 1: DDL 파일 작성**

```sql
-- scripts/sql/create_v3_tables.sql
-- v3 추천 엔진용 테이블

-- 1. 고유 장르 임베딩 (~320행)
CREATE TABLE IF NOT EXISTS genre_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  genre_text TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('l1', 'l2')),
  embedding vector(2000) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(genre_text, level)
);

-- 2. 책별 v3 벡터 (desc + L1/L2 FK)
CREATE TABLE IF NOT EXISTS book_v3_vectors (
  book_id UUID PRIMARY KEY REFERENCES books(id),
  desc_embedding vector(2000),
  source_text TEXT,
  l1_text TEXT,
  l2_text TEXT,
  l1_genre_id UUID REFERENCES genre_embeddings(id),
  l2_genre_id UUID REFERENCES genre_embeddings(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_book_v3_l1 ON book_v3_vectors(l1_genre_id);
CREATE INDEX IF NOT EXISTS idx_book_v3_l2 ON book_v3_vectors(l2_genre_id);
```

- [ ] **Step 2: Supabase SQL Editor에서 실행**

Supabase Dashboard → SQL Editor에서 위 DDL을 실행한다.
확인: `SELECT count(*) FROM genre_embeddings;` → 0
확인: `SELECT count(*) FROM book_v3_vectors;` → 0

- [ ] **Step 3: 커밋**

```bash
git add scripts/sql/create_v3_tables.sql
git commit -m "chore: v3 테이블 DDL 추가 (genre_embeddings, book_v3_vectors)"
```

---

### Task 2: 장르 파싱 모듈 + 테스트

**Files:**
- Create: `scripts/lib/genre_parser.py`
- Create: `scripts/tests/test_genre_parser.py`

- [ ] **Step 1: 테스트 작성**

```python
# scripts/tests/test_genre_parser.py
"""genre_parser 유닛 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.lib.genre_parser import parse_genre, clean_html


class TestParseGenre:
    def test_depth_4_standard(self):
        """표준 4단계 장르."""
        l1, l2 = parse_genre("국내도서>소설/시/희곡>한국소설>2000년대 이후 한국소설")
        assert l1 == "소설/시/희곡"
        assert l2 == "한국소설 2000년대 이후 한국소설"

    def test_depth_3(self):
        """3단계 장르."""
        l1, l2 = parse_genre("국내도서>경제경영>재테크/투자")
        assert l1 == "경제경영"
        assert l2 == "재테크/투자"

    def test_depth_5(self):
        """5단계 장르."""
        l1, l2 = parse_genre("국내도서>어린이>과학/수학/컴퓨터>지구와 우주>태양계")
        assert l1 == "어린이"
        assert l2 == "과학/수학/컴퓨터 지구와 우주 태양계"

    def test_foreign_prefix(self):
        """외국도서 접두어."""
        l1, l2 = parse_genre("외국도서>소설/시/희곡>영미소설")
        assert l1 == "소설/시/희곡"
        assert l2 == "영미소설"

    def test_ebook_prefix(self):
        """eBook 접두어."""
        l1, l2 = parse_genre("eBook>인문학>철학")
        assert l1 == "인문학"
        assert l2 == "철학"

    def test_empty_string(self):
        """빈 문자열 → (None, None)."""
        l1, l2 = parse_genre("")
        assert l1 is None
        assert l2 is None

    def test_none(self):
        """None → (None, None)."""
        l1, l2 = parse_genre(None)
        assert l1 is None
        assert l2 is None

    def test_depth_2_no_l2(self):
        """2단계 (L2 없음)."""
        l1, l2 = parse_genre("국내도서>경제경영")
        assert l1 == "경제경영"
        assert l2 is None

    def test_no_known_prefix(self):
        """알 수 없는 접두어 → 첫 번째를 L1으로."""
        l1, l2 = parse_genre("해외도서>소설")
        assert l1 == "해외도서"
        assert l2 == "소설"


class TestCleanHtml:
    def test_removes_tags(self):
        assert clean_html("<p>hello</p>") == "hello"

    def test_nested_tags(self):
        assert clean_html("<div><b>bold</b> text</div>") == "bold text"

    def test_empty(self):
        assert clean_html("") == ""

    def test_none(self):
        assert clean_html(None) == ""

    def test_no_tags(self):
        assert clean_html("plain text") == "plain text"

    def test_whitespace_only_after_clean(self):
        assert clean_html("<p>  </p>").strip() == ""
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python3 -m pytest scripts/tests/test_genre_parser.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.lib.genre_parser'`

- [ ] **Step 3: genre_parser.py 구현**

```python
# scripts/lib/genre_parser.py
"""장르 파싱 및 HTML 클리닝 유틸리티.

v3 추천 엔진의 L1/L2 장르 분리 규칙:
- 접두어("국내도서", "외국도서", "eBook") 제거
- L1 = 첫 번째 레벨 (중분류)
- L2 = 나머지 전부 이어붙임 (소분류 이하)
"""
import re

KNOWN_PREFIXES = {"국내도서", "외국도서", "eBook"}


def parse_genre(genre_str):
    """장르 문자열을 L1, L2로 분리.

    Returns:
        (l1, l2) 튜플. 파싱 불가하면 (None, None).
    """
    if not genre_str or not genre_str.strip():
        return None, None

    parts = [p.strip() for p in genre_str.split(">")]

    # 알려진 접두어 제거
    if parts and parts[0] in KNOWN_PREFIXES:
        parts = parts[1:]

    if not parts:
        return None, None

    l1 = parts[0] if parts else None
    l2 = " ".join(parts[1:]) if len(parts) >= 2 else None

    return l1, l2


def clean_html(text):
    """HTML 태그 제거. None이면 빈 문자열 반환."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

```bash
python3 -m pytest scripts/tests/test_genre_parser.py -v
```

Expected: 모든 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/lib/genre_parser.py scripts/tests/test_genre_parser.py
git commit -m "feat: 장르 파싱 모듈 추가 (L1/L2 분리 + clean_html)"
```

---

### Task 3: generate_genre_embeddings.py

**Files:**
- Create: `scripts/generate_genre_embeddings.py`

- [ ] **Step 1: 스크립트 작성**

```python
# scripts/generate_genre_embeddings.py
"""고유 장르 텍스트의 임베딩을 생성하여 genre_embeddings 테이블에 저장.

사용법:
  python3 scripts/generate_genre_embeddings.py          # 전체
  python3 scripts/generate_genre_embeddings.py --dry-run # 파싱만 확인, API 호출 없음
"""
import os, sys, time, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_embedding
from scripts.lib.genre_parser import parse_genre

EMBED_BATCH = 20
SLEEP_BETWEEN = 1
MAX_CONSECUTIVE_ERRORS = 3


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def collect_unique_genres(sb):
    """books 테이블에서 고유 (genre_text, level) 쌍 수집."""
    genres = set()
    offset = 0
    while True:
        res = sb.table("books").select("genre") \
            .not_.is_("genre", "null").neq("genre", "") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            l1, l2 = parse_genre(row["genre"])
            if l1:
                genres.add((l1, "l1"))
            if l2:
                genres.add((l2, "l2"))
        if len(res.data) < 1000:
            break
        offset += 1000
    return genres


def get_existing(sb):
    """이미 genre_embeddings에 있는 (genre_text, level) 쌍."""
    existing = set()
    offset = 0
    while True:
        res = sb.table("genre_embeddings").select("genre_text, level") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            existing.add((row["genre_text"], row["level"]))
        if len(res.data) < 1000:
            break
        offset += 1000
    return existing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="파싱만 확인, API 호출 없음")
    args = parser.parse_args()

    sb = make_client()

    # 1. 고유 장르 수집
    print("고유 장르 수집 중...", flush=True)
    all_genres = collect_unique_genres(sb)
    l1_count = sum(1 for _, level in all_genres if level == "l1")
    l2_count = sum(1 for _, level in all_genres if level == "l2")
    print(f"  고유 L1: {l1_count}개, L2: {l2_count}개, 합계: {len(all_genres)}개", flush=True)

    # 2. 기존 제외
    existing = get_existing(sb)
    todo = sorted(all_genres - existing)
    print(f"  이미 처리: {len(existing)}개, 남은 대상: {len(todo)}개", flush=True)

    if not todo:
        print("모든 장르가 이미 처리되었습니다.", flush=True)
        return

    if args.dry_run:
        print("\n[dry-run] 생성 대상 목록:")
        for text, level in todo:
            print(f"  [{level}] {text}")
        return

    # 3. 1건 테스트
    test_text, test_level = todo[0]
    print(f"\n사전 테스트: [{test_level}] {test_text}", flush=True)
    try:
        test_emb = call_embedding([test_text])
        assert len(test_emb) == 1 and len(test_emb[0]) == 2000
        print(f"  ✓ 임베딩 성공 (dim={len(test_emb[0])})", flush=True)
    except Exception as e:
        print(f"  ✗ 사전 테스트 실패: {e}", flush=True)
        print("  배치를 시작하지 않습니다.", flush=True)
        sys.exit(1)

    # 4. 배치 처리
    start = time.time()
    done, errors, consecutive_errors = 0, 0, 0

    for i in range(0, len(todo), EMBED_BATCH):
        batch = todo[i:i + EMBED_BATCH]
        texts = [text for text, _ in batch]

        try:
            embeddings = call_embedding(texts)
            consecutive_errors = 0
        except Exception as e:
            errors += len(batch)
            consecutive_errors += 1
            print(f"  ✗ 임베딩 실패 (연속 {consecutive_errors}회): {e}", flush=True)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n연속 에러 {MAX_CONSECUTIVE_ERRORS}회 → 자동 중단", flush=True)
                break
            time.sleep(SLEEP_BETWEEN)
            continue

        # DB 삽입
        rows = []
        for (text, level), emb in zip(batch, embeddings):
            rows.append({
                "genre_text": text,
                "level": level,
                "embedding": emb,
            })

        try:
            sb.table("genre_embeddings").insert(rows).execute()
            done += len(rows)
            consecutive_errors = 0
        except Exception as e:
            errors += len(rows)
            consecutive_errors += 1
            print(f"  ✗ DB 삽입 실패 (연속 {consecutive_errors}회): {e}", flush=True)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n연속 에러 {MAX_CONSECUTIVE_ERRORS}회 → 자동 중단", flush=True)
                break

        pct = (i + len(batch)) / len(todo) * 100
        print(f"  [{pct:5.1f}%] {done}/{len(todo)} 완료, {errors} 에러", flush=True)
        time.sleep(SLEEP_BETWEEN)

    elapsed = time.time() - start
    print(f"\n{'='*50}", flush=True)
    print(f"장르 임베딩 완료: {done}건 저장, {errors}건 에러, {elapsed:.0f}초", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: dry-run으로 파싱 결과 확인**

```bash
python3 scripts/generate_genre_embeddings.py --dry-run
```

Expected: L1 ~20개, L2 ~300개 목록 출력. 이상한 값 없는지 육안 확인.

- [ ] **Step 3: 실제 실행**

```bash
python3 -u scripts/generate_genre_embeddings.py
```

Expected: ~320개 임베딩 생성, ~16배치, ~20초 소요. 에러 0.

- [ ] **Step 4: DB 확인**

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import os
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
l1 = sb.table('genre_embeddings').select('id', count='exact').eq('level', 'l1').limit(0).execute()
l2 = sb.table('genre_embeddings').select('id', count='exact').eq('level', 'l2').limit(0).execute()
print(f'L1: {l1.count}개, L2: {l2.count}개, 합계: {l1.count + l2.count}개')
"
```

Expected: L1 ~20, L2 ~300, 합계 ~320

- [ ] **Step 5: 커밋**

```bash
git add scripts/generate_genre_embeddings.py
git commit -m "feat: 장르 임베딩 생성 스크립트 추가 + 실행 완료"
```

---

### Task 4: generate_book_v3_vectors.py

**Files:**
- Create: `scripts/generate_book_v3_vectors.py`

- [ ] **Step 1: 스크립트 작성**

```python
# scripts/generate_book_v3_vectors.py
"""책별 desc 임베딩 + L1/L2 FK를 생성하여 book_v3_vectors에 저장.

선행: genre_embeddings 테이블이 채워져 있어야 함.

사용법:
  python3 scripts/generate_book_v3_vectors.py            # 전체
  python3 scripts/generate_book_v3_vectors.py 100        # 100권만
  python3 scripts/generate_book_v3_vectors.py --dry-run  # API 호출 없이 파싱만
"""
import os, sys, time, re, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_embedding
from scripts.lib.genre_parser import parse_genre, clean_html

EMBED_BATCH = 20
SLEEP_BETWEEN = 1
MAX_CONSECUTIVE_ERRORS = 3
CHECKPOINT_INTERVAL = 100


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def build_desc_source(book):
    """desc 임베딩용 소스 텍스트 생성. 스펙 섹션 3.2."""
    source = clean_html(book.get("rich_description") or "").strip()
    if not source or len(source) < 200:
        title = book.get("title", "")
        genre = book.get("genre", "")
        desc = book.get("description", "")
        source = f"{title} ({genre}) — {desc}"
    return source[:2000]


def load_genre_lookup(sb):
    """genre_embeddings → {(genre_text, level): id} dict."""
    lookup = {}
    offset = 0
    while True:
        res = sb.table("genre_embeddings").select("id, genre_text, level") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            lookup[(row["genre_text"], row["level"])] = row["id"]
        if len(res.data) < 1000:
            break
        offset += 1000
    return lookup


def get_existing_book_ids(sb):
    """이미 book_v3_vectors에 있는 book_id 집합."""
    ids = set()
    offset = 0
    while True:
        res = sb.table("book_v3_vectors").select("book_id") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        ids.update(row["book_id"] for row in res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return ids


def fetch_target_books(sb, limit):
    """rich_description이 있는 books 조회."""
    books = []
    offset = 0
    while len(books) < limit:
        res = sb.table("books") \
            .select("id, title, genre, description, rich_description") \
            .not_.is_("rich_description", "null") \
            .range(offset, offset + 499).execute()
        if not res.data:
            break
        books.extend(res.data)
        if len(res.data) < 500:
            break
        offset += 500
    return books[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("limit", nargs="?", type=int, default=99999)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sb = make_client()

    # 1. genre lookup 로드
    print("장르 FK 로드 중...", flush=True)
    genre_lookup = load_genre_lookup(sb)
    print(f"  genre_embeddings: {len(genre_lookup)}개", flush=True)
    if not genre_lookup:
        print("  ✗ genre_embeddings가 비어있습니다. generate_genre_embeddings.py를 먼저 실행하세요.", flush=True)
        sys.exit(1)

    # 2. 대상 수집 + 기존 제외
    print("대상 도서 수집 중...", flush=True)
    all_books = fetch_target_books(sb, args.limit)
    existing = get_existing_book_ids(sb)
    books = [b for b in all_books if b["id"] not in existing]
    print(f"  전체: {len(all_books)}권, 이미 처리: {len(existing)}권, 남은 대상: {len(books)}권", flush=True)

    if not books:
        print("모든 책이 이미 처리되었습니다.", flush=True)
        return

    # 3. 준비: 각 책의 desc 소스 + L1/L2 FK 매핑
    prepared = []
    no_genre_count = 0
    for book in books:
        source = build_desc_source(book)
        l1, l2 = parse_genre(book.get("genre"))
        l1_id = genre_lookup.get((l1, "l1")) if l1 else None
        l2_id = genre_lookup.get((l2, "l2")) if l2 else None
        if not l1:
            no_genre_count += 1
        prepared.append({
            "book_id": book["id"],
            "source_text": source,
            "l1_text": l1,
            "l2_text": l2,
            "l1_genre_id": l1_id,
            "l2_genre_id": l2_id,
        })

    if no_genre_count:
        print(f"  주의: 장르 없는 책 {no_genre_count}권 (desc만 저장)", flush=True)

    if args.dry_run:
        print(f"\n[dry-run] {len(prepared)}권 준비 완료. 처음 3건:")
        for p in prepared[:3]:
            print(f"  {p['book_id'][:8]}... L1={p['l1_text']} L2={p['l2_text'][:30] if p['l2_text'] else None}")
            print(f"    desc: {p['source_text'][:80]}...")
        return

    # 4. 사전 테스트 (1건)
    test = prepared[0]
    print(f"\n사전 테스트: {test['book_id'][:8]}...", flush=True)
    try:
        test_emb = call_embedding([test["source_text"]])
        assert len(test_emb) == 1 and len(test_emb[0]) == 2000
        print(f"  ✓ 임베딩 성공 (dim={len(test_emb[0])})", flush=True)
    except Exception as e:
        print(f"  ✗ 사전 테스트 실패: {e}", flush=True)
        sys.exit(1)

    # 5. 배치 처리
    start = time.time()
    done, errors, consecutive_errors = 0, 0, 0

    for i in range(0, len(prepared), EMBED_BATCH):
        batch = prepared[i:i + EMBED_BATCH]
        texts = [p["source_text"] for p in batch]

        # 임베딩 호출
        try:
            embeddings = call_embedding(texts)
            consecutive_errors = 0
        except Exception as e:
            errors += len(batch)
            consecutive_errors += 1
            print(f"  ✗ 임베딩 실패 (연속 {consecutive_errors}회): {e}", flush=True)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n연속 에러 {MAX_CONSECUTIVE_ERRORS}회 → 자동 중단", flush=True)
                break
            time.sleep(SLEEP_BETWEEN)
            continue

        # DB 삽입
        rows = []
        for p, emb in zip(batch, embeddings):
            rows.append({
                "book_id": p["book_id"],
                "desc_embedding": emb,
                "source_text": p["source_text"],
                "l1_text": p["l1_text"],
                "l2_text": p["l2_text"],
                "l1_genre_id": p["l1_genre_id"],
                "l2_genre_id": p["l2_genre_id"],
            })

        try:
            sb.table("book_v3_vectors").insert(rows).execute()
            done += len(rows)
            consecutive_errors = 0
        except Exception as e:
            # 배치 실패 → 1건씩 재시도
            print(f"  배치 INSERT 실패, 1건씩 재시도: {e}", flush=True)
            for row in rows:
                try:
                    sb.table("book_v3_vectors").insert(row).execute()
                    done += 1
                except Exception as e2:
                    errors += 1
                    print(f"    ✗ {row['book_id'][:8]}...: {e2}", flush=True)

        # 진행률
        pct = (i + len(batch)) / len(prepared) * 100
        elapsed = time.time() - start
        rate = done / elapsed * 60 if elapsed > 0 else 0
        eta = (len(prepared) - i - len(batch)) / (rate / 60) if rate > 0 else 0
        print(f"  [{pct:5.1f}%] {done}/{len(prepared)} 완료, {errors} 에러, "
              f"{elapsed/60:.1f}분경과 ~{eta:.0f}초남음", flush=True)

        # 체크포인트 로그
        if done > 0 and done % CHECKPOINT_INTERVAL == 0:
            print(f"  ── 체크포인트: {done}건 완료 ──", flush=True)

        time.sleep(SLEEP_BETWEEN)

    elapsed = time.time() - start
    print(f"\n{'='*50}", flush=True)
    print(f"book_v3_vectors 완료: {done}건 저장, {errors}건 에러, {elapsed/60:.1f}분", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: dry-run 확인**

```bash
python3 scripts/generate_book_v3_vectors.py --dry-run
```

Expected: 2,505권 준비 완료. 처음 3건의 L1/L2/desc 확인.

- [ ] **Step 3: 소량 테스트 (10권)**

```bash
python3 -u scripts/generate_book_v3_vectors.py 10
```

Expected: 10건 성공, 에러 0. DB에서 확인:
```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import os
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
res = sb.table('book_v3_vectors').select('book_id, l1_text, l2_text', count='exact').limit(3).execute()
print(f'총 {res.count}건')
for r in res.data:
    print(f'  {r[\"book_id\"][:8]}... L1={r[\"l1_text\"]} L2={r.get(\"l2_text\", \"\")[:30]}')
"
```

- [ ] **Step 4: 전체 실행**

```bash
python3 -u scripts/generate_book_v3_vectors.py
```

Expected: ~2,505건 (10건 제외한 ~2,495건 추가), ~126배치, ~3분 소요.

- [ ] **Step 5: 커밋**

```bash
git add scripts/generate_book_v3_vectors.py
git commit -m "feat: book_v3_vectors 생성 스크립트 추가 + 2,505권 실행 완료"
```

---

### Task 5: safe_rerun.py 수정 + reason 추출 실행

**Files:**
- Modify: `scripts/safe_rerun.py`

- [ ] **Step 1: 연속 에러 중단 로직 추가**

`scripts/safe_rerun.py`의 처리 루프(line 84~)를 수정:

기존:
```python
# 3) 처리 시작
start = time.time()
done, errors = 0, 0

for i in range(0, len(ids), CHUNK):
```

변경:
```python
# 3) 처리 시작
start = time.time()
done, errors, consecutive_errors = 0, 0, 0

for i in range(0, len(ids), CHUNK):
```

기존 (line 112~118):
```python
    try:
        extractor._process_batch(books)
        done += len(books)
    except Exception as e:
        print(f"  ✗ 배치 실패: {e}", flush=True)
        errors += len(books)
        time.sleep(5)
        sb = make_client()
```

변경:
```python
    try:
        extractor._process_batch(books)
        done += len(books)
        consecutive_errors = 0
    except Exception as e:
        print(f"  ✗ 배치 실패 (연속 {consecutive_errors + 1}회): {e}", flush=True)
        errors += len(books)
        consecutive_errors += 1
        if consecutive_errors >= 3:
            print(f"\n연속 에러 3회 → 자동 중단", flush=True)
            print(f"  처리: {done}건, 에러: {errors}건", flush=True)
            break
        time.sleep(5)
        sb = make_client()
```

- [ ] **Step 2: 소량 테스트 (20권)**

```bash
python3 -u scripts/safe_rerun.py 20
```

Expected: 20권 처리 (또는 이미 처리된 경우 0권). 에러 없이 정상 종료.

- [ ] **Step 3: 전체 실행**

```bash
python3 -u scripts/safe_rerun.py
```

Expected: ~757권 처리. 2-stage LLM + 임베딩. 소요시간 ~30분 (757권 × 2초 sleep ÷ 20권/chunk).

- [ ] **Step 4: 커밋**

```bash
git add scripts/safe_rerun.py
git commit -m "fix: safe_rerun.py 연속 에러 3회 자동 중단 로직 추가"
```

---

### Task 6: 검증 스크립트 + 실행

**Files:**
- Create: `scripts/verify_v3_data.py`

- [ ] **Step 1: 검증 스크립트 작성**

```python
# scripts/verify_v3_data.py
"""v3 데이터 생성 결과 검증. 스펙 섹션 7 체크리스트."""
import os, sys, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client

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
check("L2 분포", 100 <= ge_l2 <= 500, f"L2={ge_l2}")

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
from collections import Counter
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

# 6. 서버 로딩 JOIN 테스트
print("\n서버 로딩 JOIN 시뮬레이션:")
sample = sb.table("book_v3_vectors") \
    .select("book_id, l1_text, l2_text, l1_genre_id, l2_genre_id") \
    .limit(5).execute().data
for row in sample:
    l1_ok = "✓" if row.get("l1_genre_id") else "NULL"
    l2_ok = "✓" if row.get("l2_genre_id") else "NULL"
    print(f"  {row['book_id'][:8]}... L1={l1_ok} L2={l2_ok} ({row.get('l1_text', '?')}>{row.get('l2_text', '?')[:20]})")

print(f"\n{'='*50}")
print(f"결과: {passed} 통과, {failed} 실패")
if failed:
    print("⚠️  실패한 항목을 확인하세요.")
else:
    print("✓ 모든 검증 통과")
```

- [ ] **Step 2: 검증 실행**

```bash
python3 scripts/verify_v3_data.py
```

Expected: 모든 항목 ✓ 통과.

- [ ] **Step 3: 커밋**

```bash
git add scripts/verify_v3_data.py
git commit -m "feat: v3 데이터 검증 스크립트 추가 + 전체 검증 통과"
```

---

### Task 7: ARCHITECTURE.md 업데이트 + 최종 정리

**Files:**
- Modify: `docs/ARCHITECTURE.md` (reason_embedding 3072 → 2000 수정)

- [ ] **Step 1: ARCHITECTURE.md에서 3072D 참조 수정**

`docs/ARCHITECTURE.md`에서 `3072`를 검색하여 `2000`으로 수정. 또한 신규 테이블(genre_embeddings, book_v3_vectors) 스키마 추가.

- [ ] **Step 2: project_status.md 업데이트**

`/Users/eden.huh/.claude/projects/-Users-eden-huh-Library-Mobile-Documents-iCloud-md-obsidian-Documents-Second-Brain-00-Inbox-curation/memory/project_status.md`를 업데이트하여 desc/L1/L2 데이터가 생성 완료임을 기록.

- [ ] **Step 3: 커밋 + 푸시**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: ARCHITECTURE.md v3 테이블 반영 + 임베딩 차원 수정 (3072→2000)"
git push
```
