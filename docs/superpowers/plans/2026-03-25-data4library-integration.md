# 정보나루 API 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 정보나루 `usageAnalysisList` API로 도서별 키워드 + 함께 빌린 책을 수집하여 Tier2 임베딩 품질을 향상시킨다.

**Architecture:** `data4library_collector.py` 스크립트가 `usageAnalysisList` 1콜로 키워드+co_loan을 수집하여 `books` 테이블에 저장. `daily-embed-t2.yml` 워크플로우의 선행 스텝으로 통합. `tier2_embedder.py`의 `compose_embedding()`에서 키워드를 임베딩 텍스트에 포함.

**Tech Stack:** Python, requests, Supabase, 정보나루 API, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-25-data4library-integration-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `supabase/008_library_data.sql` | Create | DB 마이그레이션 (library_keywords, related_isbns) |
| `scripts/data4library_collector.py` | Create | 정보나루 API 수집기 |
| `scripts/tests/test_data4library_collector.py` | Create | 수집기 순수 함수 테스트 |
| `scripts/tier2_embedder.py` | Modify | SELECT 절 + compose_embedding + 재임베딩 로직 |
| `scripts/tests/test_tier2_embedder.py` | Modify | compose_embedding 키워드 테스트 추가 |
| `.github/workflows/daily-embed-t2.yml` | Modify | 정보나루 수집 선행 스텝 추가 |
| `docs/ARCHITECTURE.md` | Modify | 파이프라인 흐름 + books 테이블 동기화 |

---

### Task 1: DB 마이그레이션

**Files:**
- Create: `supabase/008_library_data.sql`

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- supabase/008_library_data.sql
-- =============================================
-- 008: 정보나루 도서관 데이터 컬럼 추가
-- Spec: docs/superpowers/specs/2026-03-25-data4library-integration-design.md
-- =============================================

-- 정보나루 키워드 (compose_embedding에서 임베딩 텍스트에 포함)
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS library_keywords TEXT[];

-- 함께 빌린 책 ISBN 목록 (Phase 3 추천 엔진용, co_loan 타입 확장 대비 jsonb)
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS related_isbns JSONB;
```

- [ ] **Step 2: Supabase에 마이그레이션 적용**

Supabase Dashboard → SQL Editor에서 실행하거나:
```bash
# 로컬에서 직접 적용 (Supabase CLI 사용 시)
supabase db push
```

- [ ] **Step 3: 커밋**

```bash
git add supabase/008_library_data.sql
git commit -m "feat: 정보나루 데이터 컬럼 마이그레이션 — library_keywords, related_isbns"
```

---

### Task 2: data4library_collector 순수 함수 + 테스트

**Files:**
- Create: `scripts/data4library_collector.py` (순수 함수 부분만)
- Create: `scripts/tests/test_data4library_collector.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# scripts/tests/test_data4library_collector.py
"""정보나루 수집기 순수 함수 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestParseKeywords:
    """API 응답에서 키워드 파싱"""

    def test_parse_keywords_normal(self):
        from data4library_collector import parse_keywords
        response = {
            "response": {
                "keywords": [
                    {"keyword": {"word": "인생"}},
                    {"keyword": {"word": "성장"}},
                    {"keyword": {"word": "자아찾기"}},
                ]
            }
        }
        assert parse_keywords(response) == ["인생", "성장", "자아찾기"]

    def test_parse_keywords_empty(self):
        from data4library_collector import parse_keywords
        assert parse_keywords({}) == []
        assert parse_keywords({"response": {}}) == []
        assert parse_keywords(None) == []

    def test_parse_keywords_missing_word(self):
        from data4library_collector import parse_keywords
        response = {
            "response": {
                "keywords": [
                    {"keyword": {"word": "인생"}},
                    {"keyword": {}},
                ]
            }
        }
        assert parse_keywords(response) == ["인생"]


class TestParseCoLoanBooks:
    """API 응답에서 함께 빌린 책 ISBN 파싱"""

    def test_parse_co_loan_normal(self):
        from data4library_collector import parse_co_loan_books
        response = {
            "response": {
                "coLoanBooks": [
                    {"book": {"isbn13": "9788932920993"}},
                    {"book": {"isbn13": "9788936434120"}},
                ]
            }
        }
        assert parse_co_loan_books(response) == ["9788932920993", "9788936434120"]

    def test_parse_co_loan_empty(self):
        from data4library_collector import parse_co_loan_books
        assert parse_co_loan_books({}) == []
        assert parse_co_loan_books(None) == []

    def test_parse_co_loan_capped_at_50(self):
        from data4library_collector import parse_co_loan_books
        books = [{"book": {"isbn13": f"978893292{i:04d}"}} for i in range(80)]
        response = {"response": {"coLoanBooks": books}}
        result = parse_co_loan_books(response)
        assert len(result) == 50

    def test_parse_co_loan_missing_isbn(self):
        from data4library_collector import parse_co_loan_books
        response = {
            "response": {
                "coLoanBooks": [
                    {"book": {"isbn13": "9788932920993"}},
                    {"book": {}},
                    {"book": {"isbn13": "9788936434120"}},
                ]
            }
        }
        assert parse_co_loan_books(response) == ["9788932920993", "9788936434120"]
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd scripts && python -m pytest tests/test_data4library_collector.py -v
```
Expected: FAIL (모듈 미존재)

- [ ] **Step 3: 순수 함수 구현**

```python
# scripts/data4library_collector.py (상단 + 순수 함수 부분)
"""
정보나루 도서관 데이터 수집기

usageAnalysisList API 1콜로 키워드 + 함께 빌린 책을 동시 수집.
키워드는 Tier2 임베딩 보강용, 연관도서는 Phase 3 추천 엔진용.

사용법:
  python3 scripts/data4library_collector.py                  # 기본 (300권)
  python3 scripts/data4library_collector.py --limit 50       # 50권만
  python3 scripts/data4library_collector.py --limit 10000    # 백필
  python3 scripts/data4library_collector.py --status          # 진행 현황
  python3 scripts/data4library_collector.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install requests supabase python-dotenv
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    import requests
except ImportError:
    pass

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()


CO_LOAN_CAP = 50
REQUEST_DELAY = 0.5


# --- 순수 함수 (테스트 가능) ---

def parse_keywords(response):
    """API 응답에서 키워드 리스트 추출."""
    if not response:
        return []
    try:
        keywords = response.get("response", {}).get("keywords", [])
        return [
            kw["keyword"]["word"]
            for kw in keywords
            if kw.get("keyword", {}).get("word")
        ]
    except (KeyError, TypeError):
        return []


def parse_co_loan_books(response):
    """API 응답에서 함께 빌린 책 ISBN 리스트 추출. 최대 50개."""
    if not response:
        return []
    try:
        books = response.get("response", {}).get("coLoanBooks", [])
        isbns = [
            b["book"]["isbn13"]
            for b in books
            if b.get("book", {}).get("isbn13")
        ]
        return isbns[:CO_LOAN_CAP]
    except (KeyError, TypeError):
        return []
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd scripts && python -m pytest tests/test_data4library_collector.py -v
```
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/data4library_collector.py scripts/tests/test_data4library_collector.py
git commit -m "feat: 정보나루 수집기 순수 함수 + 테스트 — parse_keywords, parse_co_loan_books"
```

---

### Task 3: data4library_collector 클래스 구현

**Files:**
- Modify: `scripts/data4library_collector.py` (클래스 + CLI 추가)

**사전 조건:** 로컬 `.env`에 `DATA4LIBRARY_API_KEY`가 이미 존재하는지 확인. 없으면 추가 필요.

- [ ] **Step 1: Data4LibraryCollector 클래스 추가**

`scripts/data4library_collector.py`의 순수 함수 아래에 클래스 추가:

```python
# --- 수집기 클래스 ---

class Data4LibraryCollector:
    DEFAULT_LIMIT = 300
    API_BASE = "http://data4library.kr/api"

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self._api_key = None  # lazy init — --status에서는 불필요
        self.stats = {
            "processed": 0, "keywords_found": 0,
            "co_loan_found": 0, "empty": 0, "errors": 0,
        }

    @property
    def api_key(self):
        if self._api_key is None:
            self._api_key = os.getenv("DATA4LIBRARY_API_KEY")
            if not self._api_key:
                print("❌ DATA4LIBRARY_API_KEY 환경변수가 설정되지 않았습니다.")
                sys.exit(1)
        return self._api_key

    def fetch_books_needing_collection(self, limit):
        """library_keywords가 NULL인 책 조회 (sales_point 높은 순)."""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("books")
                .select("id, isbn")
                .is_("library_keywords", "null")
                .not_.is_("isbn", "null")
                .order("sales_point", desc=True)
                .range(o, o + page_size - 1)
                .execute()
            ))
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size or len(all_books) >= limit:
                break
            offset += page_size
        return all_books[:limit]

    def fetch_usage(self, isbn):
        """usageAnalysisList API 호출 → (keywords, co_loan_isbns)."""
        url = f"{self.API_BASE}/usageAnalysisList"
        params = {
            "authKey": self.api_key,
            "isbn13": isbn,
            "format": "json",
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            keywords = parse_keywords(data)
            co_loan = parse_co_loan_books(data)
            return keywords, co_loan
        except Exception as e:
            raise RuntimeError(f"API 호출 실패 ({isbn}): {e}")

    def _save(self, book_id, keywords, co_loan_isbns):
        """books 테이블에 키워드 + 연관도서 저장."""
        if self.dry_run:
            return
        update = {"library_keywords": keywords if keywords else []}
        if co_loan_isbns:
            update["related_isbns"] = {"co_loan": co_loan_isbns}
        with_retry(lambda: (
            self.sb.table("books")
            .update(update)
            .eq("id", book_id)
            .execute()
        ))

    def run(self, limit=None):
        """메인 실행."""
        limit = limit or self.DEFAULT_LIMIT
        print(f"🔍 정보나루 수집 대상 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_collection(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서의 정보나루 데이터가 수집 완료됨.")
            return

        for i, book in enumerate(books):
            isbn = book["isbn"]
            try:
                keywords, co_loan = self.fetch_usage(isbn)

                self._save(book["id"], keywords, co_loan)

                self.stats["processed"] += 1
                if keywords:
                    self.stats["keywords_found"] += 1
                if co_loan:
                    self.stats["co_loan_found"] += 1
                if not keywords and not co_loan:
                    self.stats["empty"] += 1

                if self.stats["processed"] % 50 == 0 or self.stats["processed"] <= 3:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{self.stats['processed']}/{len(books)}: "
                          f"kw={len(keywords)} co={len(co_loan)}")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({isbn}): {e}")

            time.sleep(REQUEST_DELAY)

        self._print_report(len(books))

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}정보나루 수집 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}권")
        print(f"  처리 완료: {s['processed']}권")
        print(f"  키워드 있음: {s['keywords_found']}권")
        print(f"  연관도서 있음: {s['co_loan_found']}권")
        print(f"  데이터 없음: {s['empty']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = with_retry(lambda: self.sb.table("books")
                           .select("id", count="exact").execute())
        has_kw = with_retry(lambda: self.sb.table("books")
                            .select("id", count="exact")
                            .not_.is_("library_keywords", "null").execute())
        has_rel = with_retry(lambda: self.sb.table("books")
                             .select("id", count="exact")
                             .not_.is_("related_isbns", "null").execute())

        kw_pct = has_kw.count * 100 // total.count if total.count else 0
        rel_pct = has_rel.count * 100 // total.count if total.count else 0

        print(f"\n{'=' * 50}")
        print("정보나루 수집 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  키워드 수집 완료: {has_kw.count}권 ({kw_pct}%)")
        print(f"  키워드 미수집: {total.count - has_kw.count}권")
        print(f"  연관도서 있음: {has_rel.count}권 ({rel_pct}%)")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="정보나루 도서관 데이터 수집기")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수 (기본 300)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="진행 현황")
    args = parser.parse_args()

    collector = Data4LibraryCollector(dry_run=args.dry_run)

    if args.status:
        collector.show_status()
        return

    collector.run(limit=args.limit)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 로컬 dry-run 테스트**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python scripts/data4library_collector.py --dry-run --limit 3
```
Expected: 3권 처리, API 호출 성공, DB 저장 없음

- [ ] **Step 3: --status 테스트**

```bash
python scripts/data4library_collector.py --status
```
Expected: 전체 도서 수, 키워드 수집 0권 (아직 미수집)

- [ ] **Step 4: 커밋**

```bash
git add scripts/data4library_collector.py
git commit -m "feat: 정보나루 수집기 클래스 + CLI — usageAnalysisList 1콜로 키워드/co_loan 수집"
```

---

### Task 4: tier2_embedder 수정 — 키워드 임베딩 연동

**Files:**
- Modify: `scripts/tier2_embedder.py:100-150` (compose_embedding 주석 해제)
- Modify: `scripts/tier2_embedder.py:169-212` (fetch_books_needing_tier2 SELECT + 재임베딩)
- Modify: `scripts/tests/test_tier2_embedder.py` (키워드 테스트 추가)

- [ ] **Step 1: compose_embedding 키워드 테스트 추가**

`scripts/tests/test_tier2_embedder.py` 끝에 추가:

```python
class TestComposeEmbeddingWithKeywords:
    """키워드가 임베딩 텍스트에 포함되는지 검증"""

    def test_with_library_keywords(self):
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '설명',
            'rich_description': '[책소개]\n멋진 소설이다.',
            'library_keywords': ['인생', '성장', '자아찾기'],
        }
        text, sources = compose_embedding(book)
        assert '키워드: 인생, 성장, 자아찾기' in text
        assert 'library_keywords' in sources

    def test_without_library_keywords(self):
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '설명',
            'rich_description': '[책소개]\n멋진 소설이다.',
            'library_keywords': None,
        }
        text, sources = compose_embedding(book)
        assert '키워드:' not in text
        assert 'library_keywords' not in sources

    def test_empty_library_keywords(self):
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '설명',
            'rich_description': '[책소개]\n멋진 소설이다.',
            'library_keywords': [],
        }
        text, sources = compose_embedding(book)
        assert '키워드:' not in text
        assert 'library_keywords' not in sources
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd scripts && python -m pytest tests/test_tier2_embedder.py::TestComposeEmbeddingWithKeywords -v
```
Expected: FAIL (키워드 코드가 주석 상태)

- [ ] **Step 3: compose_embedding() 주석 해제**

`scripts/tier2_embedder.py` line 139~142의 주석을 해제:

```python
    # 변경 전 (주석)
    # (미래) 도서관 키워드
    # if book.get('library_keywords'):
    #     parts.append(f"키워드: {', '.join(book['library_keywords'])}")
    #     data_sources.append('library_keywords')

    # 변경 후
    if book.get('library_keywords'):
        parts.append(f"키워드: {', '.join(book['library_keywords'])}")
        data_sources.append('library_keywords')
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd scripts && python -m pytest tests/test_tier2_embedder.py -v
```
Expected: 전체 통과 (기존 14 + 신규 3 = 17 tests)

- [ ] **Step 5: fetch_books_needing_tier2 SELECT 절 수정**

`scripts/tier2_embedder.py` line 175:

```python
    # 변경 전
    .select("id, title, author, genre, description, rich_description")
    # 변경 후
    .select("id, title, author, genre, description, rich_description, library_keywords")
```

- [ ] **Step 6: 재임베딩 대상 로직 수정**

`scripts/tier2_embedder.py`의 `fetch_books_needing_tier2()` 메서드, line 187~207:

```python
        if force:
            books = all_books
        else:
            # 이미 Tier 2 임베딩이 있는 book_id → data_sources 조회
            tier2_map = {}  # book_id → data_sources
            offset = 0
            while True:
                result = with_retry(lambda o=offset: self.sb.table("book_embeddings") \
                    .select("book_id, data_sources") \
                    .eq("tier", 2) \
                    .range(o, o + page_size - 1) \
                    .execute())
                if not result.data:
                    break
                for row in result.data:
                    tier2_map[row["book_id"]] = row.get("data_sources", [])
                if len(result.data) < page_size:
                    break
                offset += page_size

            books = []
            for b in all_books:
                if b["id"] not in tier2_map:
                    # Tier 2 임베딩 없음 → 대상
                    books.append(b)
                elif b.get("library_keywords") and \
                     "library_keywords" not in (tier2_map[b["id"]] or []):
                    # 키워드 있지만 임베딩에 미반영 → 재임베딩 대상
                    books.append(b)
```

- [ ] **Step 7: 전체 테스트 실행**

```bash
cd scripts && python -m pytest tests/ -v
```
Expected: 전체 통과

- [ ] **Step 8: 커밋**

```bash
git add scripts/tier2_embedder.py scripts/tests/test_tier2_embedder.py
git commit -m "feat: tier2_embedder 키워드 임베딩 연동 — compose_embedding 활성화 + 재임베딩 로직"
```

---

### Task 5: 워크플로우 통합 + GitHub Secrets

**Files:**
- Modify: `.github/workflows/daily-embed-t2.yml`

- [ ] **Step 1: daily-embed-t2.yml에 정보나루 수집 스텝 추가**

`Install dependencies` 스텝 뒤, `Run Tier 2 embedder` 스텝 앞에 추가:

```yaml
      - name: Collect library keywords & co-loan data
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          DATA4LIBRARY_API_KEY: ${{ secrets.DATA4LIBRARY_API_KEY }}
        run: python scripts/data4library_collector.py --limit 300

      - name: Show collection status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/data4library_collector.py --status
```

- [ ] **Step 2: GitHub Secrets에 DATA4LIBRARY_API_KEY 추가**

```bash
gh secret set DATA4LIBRARY_API_KEY
# 프롬프트에 키 값 입력
```

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/daily-embed-t2.yml
git commit -m "ci: daily-embed-t2에 정보나루 수집 선행 스텝 추가"
```

---

### Task 6: ARCHITECTURE.md 동기화

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: 파이프라인 흐름에 정보나루 추가**

`docs/ARCHITECTURE.md`의 데이터 흐름 섹션 (line 66~73) 중 daily-embed-t2 설명을 수정:

```
매일 KST 06:30 (daily-embed-t2):
  → 정보나루 키워드/연관도서 수집 300권 (usageAnalysisList → library_keywords, related_isbns)
  → Tier 2 임베딩 생성 (rich_description + library_keywords 기반 → book_embeddings 업그레이드)
```

- [ ] **Step 2: books 테이블에 컬럼 추가**

books 테이블 상세 (line 225~247)에 추가:

```
| library_keywords | text[] | 정보나루 키워드 (예: {"인생","성장","자아찾기"}) |
| related_isbns | jsonb | 함께 빌린 책 ISBN (예: {"co_loan": ["978..."]}) |
```

- [ ] **Step 3: 커밋**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: ARCHITECTURE.md 정보나루 파이프라인 + books 컬럼 동기화"
```

---

### Task 7: 백필 실행

**Files:** 없음 (운영 작업)

- [ ] **Step 1: 소량 테스트 (10권)**

```bash
python scripts/data4library_collector.py --limit 10
```
Expected: 10권 처리, 키워드/co_loan 수집 결과 출력

- [ ] **Step 2: 현황 확인**

```bash
python scripts/data4library_collector.py --status
```
Expected: 키워드 수집 완료 ~10권

- [ ] **Step 3: 전체 백필 실행**

```bash
python scripts/data4library_collector.py --limit 10000
```
Expected: ~72분 소요, 8,589권 처리

- [ ] **Step 4: 백필 후 현황 확인**

```bash
python scripts/data4library_collector.py --status
python scripts/tier2_embedder.py --status
```

- [ ] **Step 5: 재임베딩 (rich_description이 있는 책)**

```bash
python scripts/tier2_embedder.py --force --limit 9000
```
Expected: rich_description이 있는 책만 키워드 포함하여 재임베딩
