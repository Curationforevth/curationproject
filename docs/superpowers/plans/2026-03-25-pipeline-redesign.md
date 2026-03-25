# 파이프라인 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YES24 스크래퍼를 requests+BS4로 교체하고, 워크플로우를 3개로 분리하며, 모든 Supabase 호출에 retry를 추가하여 파이프라인 안정성과 처리량을 개선한다.

**Architecture:** 기존 2개 워크플로우(daily-batch, daily-enrich)를 3개(daily-collect, daily-scrape, daily-embed-t2)로 재편. YES24 스크래퍼는 Playwright→requests로 전환하고 2시간 간격 분산 실행. 공통 retry 모듈로 Supabase 일시 장애 자동 복구.

**Tech Stack:** Python 3.12, requests, BeautifulSoup4, Supabase Python SDK, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-25-pipeline-redesign.md`

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `scripts/lib/retry.py` | Supabase retry wrapper (exponential backoff + jitter) |
| Create | `scripts/tests/conftest.py` | 테스트 공통 sys.path 설정 |
| Create | `scripts/tests/test_retry.py` | retry 모듈 단위 테스트 |
| Create | `scripts/tests/test_yes24_scraper.py` | YES24 스크래퍼 단위 테스트 (순수 함수) |
| Rewrite | `scripts/yes24_scraper.py` | Playwright→requests+BS4, ISBN 순회 매칭 |
| Modify | `scripts/lib/state_manager.py` | retry wrapper 적용 |
| Modify | `scripts/smart_batch_collector.py` | retry wrapper 적용 |
| Modify | `scripts/batch_enricher.py` | retry wrapper 적용 |
| Modify | `scripts/tier1_embedder.py` | retry wrapper 적용 |
| Modify | `scripts/tier2_embedder.py` | retry wrapper 적용 |
| Modify | `scripts/requirements.txt` | +requests, +beautifulsoup4 |
| Rename+Rewrite | `.github/workflows/daily-batch.yml` → `daily-collect.yml` | 수집+보강+T1 임베딩, continue-on-error |
| Delete | `.github/workflows/daily-enrich.yml` | 3개 워크플로우로 대체 |
| Create | `.github/workflows/daily-scrape.yml` | YES24 분산 스크래핑 (2시간마다) |
| Create | `.github/workflows/daily-embed-t2.yml` | Tier 2 임베딩 (KST 06:30) |
| Modify | `docs/ARCHITECTURE.md` | 파이프라인 섹션 업데이트 |

---

### Task 1: Supabase Retry Wrapper

**Files:**
- Create: `scripts/lib/retry.py`
- Create: `scripts/tests/test_retry.py`

- [ ] **Step 0: Create conftest.py for test path setup**

```python
# scripts/tests/conftest.py
"""테스트 공통 설정 — scripts/를 sys.path에 추가"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
```

- [ ] **Step 1: Write failing tests for retry module**

```python
# scripts/tests/test_retry.py
"""retry wrapper 단위 테스트"""
import pytest
from unittest.mock import MagicMock
from lib.retry import with_retry


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"API error {code}")


def test_success_on_first_try():
    fn = MagicMock(return_value="ok")
    result = with_retry(fn)
    assert result == "ok"
    assert fn.call_count == 1


def test_retry_on_502_then_success():
    fn = MagicMock(side_effect=[FakeAPIError(502), "ok"])
    result = with_retry(fn, base_delay=0.01)
    assert result == "ok"
    assert fn.call_count == 2


def test_retry_on_connection_error_then_success():
    fn = MagicMock(side_effect=[ConnectionError("reset"), "ok"])
    result = with_retry(fn, base_delay=0.01)
    assert result == "ok"
    assert fn.call_count == 2


def test_no_retry_on_4xx():
    fn = MagicMock(side_effect=FakeAPIError(404))
    with pytest.raises(FakeAPIError):
        with_retry(fn, base_delay=0.01)
    assert fn.call_count == 1


def test_exhausts_retries():
    fn = MagicMock(side_effect=FakeAPIError(502))
    with pytest.raises(FakeAPIError):
        with_retry(fn, max_retries=3, base_delay=0.01)
    assert fn.call_count == 4  # 1 initial + 3 retries
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts && python -m pytest tests/test_retry.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement retry module**

```python
# scripts/lib/retry.py
"""Supabase 호출 retry wrapper — exponential backoff + jitter"""
import random
import time


def _is_retryable(exc):
    """재시도 대상 에러인지 판별"""
    # ConnectionError, TimeoutError
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    # postgrest.exceptions.APIError — code 속성으로 판별
    code = getattr(exc, 'code', None)
    if code in (502, 503, 504):
        return True
    return False


def with_retry(fn, max_retries=3, base_delay=1.0):
    """fn()을 호출하고, 재시도 가능한 에러 시 exponential backoff로 재시도.

    재시도 대상: 502, 503, 504, ConnectionError, TimeoutError, OSError
    즉시 실패: 그 외 모든 에러 (4xx 등)

    Args:
        fn: 인자 없는 callable (lambda로 감싸서 전달)
        max_retries: 최대 재시도 횟수 (기본 3)
        base_delay: 첫 재시도 대기 시간 (초, 기본 1.0)
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt) * (1 + random.uniform(-0.3, 0.3))
            print(f"  ⚠ Supabase 재시도 {attempt + 1}/{max_retries} ({type(e).__name__}), {delay:.1f}초 대기...")
            time.sleep(delay)
    raise last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts && python -m pytest tests/test_retry.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/retry.py scripts/tests/test_retry.py
git commit -m "feat: Supabase retry wrapper — exponential backoff + jitter"
```

---

### Task 2: YES24 스크래퍼 전면 교체

**Files:**
- Rewrite: `scripts/yes24_scraper.py`
- Create: `scripts/tests/test_yes24_scraper.py`

- [ ] **Step 1: Write tests for pure functions (ISBN 매칭, 텍스트 추출)**

```python
# scripts/tests/test_yes24_scraper.py
"""YES24 스크래퍼 순수 함수 테스트"""
import pytest


def test_isbn_matches_exact():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "9788954681179") is True


def test_isbn_matches_partial():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "8954681179") is True


def test_isbn_no_match():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "9788935679188") is False


def test_isbn_non_standard_k_prefix():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788925588735", "K442137004") is False


def test_isbn_empty():
    from yes24_scraper import isbn_matches
    assert isbn_matches("", "9788954681179") is False
    assert isbn_matches("9788954681179", "") is False


def test_is_non_standard_isbn():
    from yes24_scraper import is_non_standard_isbn
    assert is_non_standard_isbn("K442137004") is True
    assert is_non_standard_isbn("12345") is True
    assert is_non_standard_isbn("9788954681179") is False


def test_build_search_query():
    from yes24_scraper import build_search_query
    assert build_search_query("데미안 (오리지널 초판본 표지디자인)", "헤르만 헤세 (지은이)") == "데미안 헤르만 헤세"


def test_build_search_query_multiple_authors():
    from yes24_scraper import build_search_query
    assert build_search_query("숨결이 바람 될 때", "폴 칼라니티, 이종인 (옮긴이)") == "숨결이 바람 될 때 폴 칼라니티"


def test_clean_section_text():
    from yes24_scraper import clean_section_text
    raw = "책소개\n좋은 책입니다.\n접기\n펼쳐보기"
    result = clean_section_text(raw)
    assert result == "좋은 책입니다."


def test_clean_section_text_too_short():
    from yes24_scraper import clean_section_text
    assert clean_section_text("짧음") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts && python -m pytest tests/test_yes24_scraper.py -v`
Expected: FAIL (functions not found)

- [ ] **Step 3: Rewrite yes24_scraper.py with requests+BS4**

`scripts/yes24_scraper.py` 전체를 아래로 교체:

```python
"""
YES24 책 상세 텍스트 스크래퍼 (requests + BeautifulSoup 버전)

books 테이블에서 rich_description이 NULL인 책을 찾아
YES24에서 책소개 + 출판사리뷰 + 책속으로를 스크래핑.

분산 실행: 2시간마다 80권씩 (GitHub Actions cron).

사용법:
  python3 scripts/yes24_scraper.py                  # 기본 (80권)
  python3 scripts/yes24_scraper.py --limit 50       # 50권만
  python3 scripts/yes24_scraper.py --status          # 진행 현황
  python3 scripts/yes24_scraper.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install requests beautifulsoup4 supabase python-dotenv
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    pass  # --status 등에서는 불필요

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()

# --- 순수 함수 (테스트 가능) ---

UI_NOISE = {'책소개', '출판사 리뷰', '책 속으로', '접기', '펼쳐보기', '더보기'}


def isbn_matches(page_isbn, db_isbn):
    """YES24 페이지 ISBN과 DB ISBN 비교"""
    if not page_isbn or not db_isbn:
        return False
    if is_non_standard_isbn(db_isbn):
        return False
    if len(page_isbn) >= 13 and len(db_isbn) >= 13:
        return page_isbn[-13:] == db_isbn[-13:]
    return page_isbn[-12:] == db_isbn[-12:]


def is_non_standard_isbn(isbn):
    """비표준 ISBN 여부 (K prefix, 10자 미만)"""
    if not isbn:
        return True
    return isbn.startswith('K') or len(isbn) < 10


def build_search_query(title, author):
    """검색 쿼리 생성: 제목 핵심부 + 첫 번째 저자"""
    clean_author = re.sub(r'\s*\(.*?\)', '', author or '').strip().split(',')[0].strip()
    core_title = title.split(' - ')[0].split(' :')[0].split(' (')[0].strip()
    return f"{core_title} {clean_author}".strip()


def clean_section_text(raw_text):
    """UI 노이즈 제거 후 텍스트 반환. 20자 미만이면 None."""
    lines = raw_text.split('\n')
    cleaned = '\n'.join(l for l in lines if l.strip() not in UI_NOISE)
    return cleaned.strip() if len(cleaned.strip()) > 20 else None


def extract_isbn_from_html(html):
    """HTML에서 JSON-LD의 ISBN 추출"""
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            ld = json.loads(m.group(1))
            return ld.get('gtin13') or ld.get('isbn', '')
        except (json.JSONDecodeError, KeyError):
            pass
    return None


# --- 스크래퍼 클래스 ---

class Yes24Scraper:
    DEFAULT_LIMIT = 80  # 분산 실행 기준 (2시간마다)
    REQUEST_DELAY = 1.0  # 요청 간 딜레이 (초)
    MAX_SEARCH_RESULTS = 5  # ISBN 매칭 시 확인할 검색 결과 수
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
    }

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self._session = None  # lazy init — --status에서는 requests 불필요
        self.stats = {
            "processed": 0, "success": 0, "search_fail": 0,
            "isbn_mismatch": 0, "isbn_skip": 0, "scrape_fail": 0, "errors": 0,
        }

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self.HEADERS)
        return self._session

    def fetch_books_needing_scrape(self, limit):
        """rich_description이 NULL인 책 조회"""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("books")
                .select("id, isbn, title, author")
                .is_("rich_description", "null")
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

    def _search_goods_ids(self, title, author):
        """YES24 검색 → goods ID 리스트 반환 (최대 MAX_SEARCH_RESULTS건)"""
        query = build_search_query(title, author)
        url = f'https://www.yes24.com/Product/Search?domain=BOOK&query={urllib.parse.quote(query)}'
        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            els = soup.select('[data-goods-no]')
            return [el.get('data-goods-no') for el in els[:self.MAX_SEARCH_RESULTS]]
        except Exception:
            return []

    def _fetch_detail_page(self, goods_id):
        """상세 페이지 HTML 반환"""
        try:
            r = self.session.get(f'https://www.yes24.com/Product/Goods/{goods_id}', timeout=10)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    def _find_matching_page(self, goods_ids, expected_isbn):
        """goods ID 리스트를 순회하며 ISBN 일치하는 상세 페이지 HTML 반환"""
        for goods_id in goods_ids:
            html = self._fetch_detail_page(goods_id)
            if not html:
                continue

            page_isbn = extract_isbn_from_html(html)

            # ISBN 일치
            if isbn_matches(page_isbn, expected_isbn):
                return html

            # JSON-LD 없는 경우 첫 번째 결과를 fallback으로 사용
            if page_isbn is None:
                return html

            time.sleep(0.3)  # 순회 중 짧은 딜레이

        return None

    def _extract_sections(self, html):
        """HTML에서 책소개/출판사리뷰/책속으로 추출"""
        soup = BeautifulSoup(html, 'html.parser')
        sections = {}
        for sid, name in [
            ('infoset_introduce', '책소개'),
            ('infoset_pubReivew', '출판사리뷰'),
            ('infoset_inBook', '책속으로'),
        ]:
            el = soup.select_one(f'#{sid}')
            if el:
                raw = el.get_text(separator='\n', strip=True)
                cleaned = clean_section_text(raw)
                if cleaned:
                    sections[name] = cleaned
        return sections

    def _save_rich_description(self, book_id, sections):
        """DB에 rich_description 저장"""
        if not sections or self.dry_run:
            return
        combined = '\n\n'.join(f'[{name}]\n{text}' for name, text in sections.items())
        with_retry(lambda: (
            self.sb.table("books")
            .update({"rich_description": combined})
            .eq("id", book_id)
            .execute()
        ))

    def run(self, limit=None):
        """메인 실행"""
        limit = limit or self.DEFAULT_LIMIT
        print(f"🔍 스크래핑 필요한 도서 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_scrape(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서가 스크래핑 완료됨.")
            return

        for i, book in enumerate(books):
            title = book['title']
            author = book.get('author', '') or ''
            isbn = book['isbn']

            try:
                # 비표준 ISBN 조기 스킵
                if is_non_standard_isbn(isbn):
                    self.stats["isbn_skip"] += 1
                    continue

                # 1. YES24 검색
                goods_ids = self._search_goods_ids(title, author)
                if not goods_ids:
                    self.stats["search_fail"] += 1
                    if self.stats["search_fail"] <= 10:
                        print(f"  ✗ 검색 실패: {title[:35]}")
                    time.sleep(self.REQUEST_DELAY)
                    continue

                time.sleep(self.REQUEST_DELAY)

                # 2. ISBN 매칭 (상위 5건 순회)
                html = self._find_matching_page(goods_ids, isbn)
                if not html:
                    self.stats["isbn_mismatch"] += 1
                    if self.stats["isbn_mismatch"] <= 5:
                        print(f"  ⚠ ISBN 불일치: {title[:35]}")
                    time.sleep(self.REQUEST_DELAY)
                    continue

                # 3. 텍스트 추출
                sections = self._extract_sections(html)
                if not sections:
                    self.stats["scrape_fail"] += 1
                    time.sleep(self.REQUEST_DELAY)
                    continue

                # 4. DB 저장
                self._save_rich_description(book['id'], sections)

                total_chars = sum(len(t) for t in sections.values())
                self.stats["success"] += 1
                self.stats["processed"] += 1

                if self.stats["success"] % 25 == 0 or self.stats["success"] <= 5:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{self.stats['success']}/{len(books)}: "
                          f"{title[:25]} — {total_chars}자")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러: {title[:25]} — {e}")

            time.sleep(self.REQUEST_DELAY)

        self._print_report(len(books))

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}YES24 스크래핑 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}권")
        print(f"  성공: {s['success']}권")
        print(f"  검색 실패: {s['search_fail']}권")
        print(f"  ISBN 불일치: {s['isbn_mismatch']}권")
        print(f"  비표준 ISBN 스킵: {s['isbn_skip']}권")
        print(f"  스크래핑 실패: {s['scrape_fail']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = with_retry(lambda: self.sb.table("books").select("id", count="exact").execute())
        has_rich = with_retry(lambda: (
            self.sb.table("books").select("id", count="exact")
            .not_.is_("rich_description", "null").execute()
        ))
        pct = has_rich.count * 100 // total.count if total.count else 0
        print(f"\n{'=' * 50}")
        print("YES24 스크래핑 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  rich_description 완료: {has_rich.count}권 ({pct}%)")
        print(f"  스크래핑 필요: {total.count - has_rich.count}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="YES24 책 상세 스크래퍼")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수 (기본 80)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="진행 현황")
    args = parser.parse_args()

    scraper = Yes24Scraper(dry_run=args.dry_run)

    if args.status:
        scraper.show_status()
        return

    scraper.run(limit=args.limit)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `cd scripts && python -m pytest tests/test_yes24_scraper.py -v`
Expected: 10 passed

- [ ] **Step 5: Run integration test (dry-run 10권)**

Run: `cd scripts && python yes24_scraper.py --limit 10 --dry-run`
Expected: 성공 8-10권, 에러 0건

- [ ] **Step 6: Commit**

```bash
git add scripts/yes24_scraper.py scripts/tests/test_yes24_scraper.py
git commit -m "feat: YES24 스크래퍼 requests+BS4로 전환 — ISBN 순회 매칭"
```

---

### Task 3: 기존 스크립트에 Retry Wrapper 적용

**Files:**
- Modify: `scripts/lib/state_manager.py`
- Modify: `scripts/smart_batch_collector.py`
- Modify: `scripts/batch_enricher.py`
- Modify: `scripts/tier1_embedder.py`
- Modify: `scripts/tier2_embedder.py`

- [ ] **Step 1: state_manager.py에 retry 적용**

`scripts/lib/state_manager.py` 수정:

```python
# 파일 상단에 import 추가 (같은 lib 패키지 내 상대 import)
from .retry import with_retry
```

`get_state()` 메서드의 `result = q.execute()` 를:
```python
result = with_retry(lambda: q.execute())
```

`upsert_state()` 메서드의 `.execute()` 를:
```python
with_retry(lambda: self.sb.table(self.table).upsert(row, on_conflict="source_type,query_type,category_id,search_keyword").execute())
```

`reset_expired_states()`, `get_all_states()` 의 `.execute()` 도 동일하게 `with_retry(lambda: ...)` 로 감싸기.

- [ ] **Step 2: smart_batch_collector.py에 retry 적용**

파일 상단에 import 추가 (모든 스크립트에서 동일한 패턴 사용):
```python
try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()
```

`.execute()` 호출이 있는 Supabase 쿼리를 `with_retry(lambda: ...)` 로 감싸기. 대상:
- `load_known_isbns()` 의 `result = q.execute()`
- `save_batch()` 의 `.upsert(...).execute()`
- `save_batch()` 의 개별 fallback `.upsert(...).execute()`

- [ ] **Step 3: batch_enricher.py에 retry 적용**

파일 상단에 import 추가 (동일 패턴):
```python
try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()
```

`.execute()` 호출을 `with_retry(lambda: ...)` 로 감싸기. 대상:
- 보강 대상 조회 쿼리
- 개별 도서 업데이트 쿼리
- `show_status()` 의 count 쿼리

- [ ] **Step 4: tier1_embedder.py에 retry 적용**

파일 상단에 import 추가:
```python
try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()
```

`.execute()` 호출을 `with_retry(lambda: ...)` 로 감싸기. 대상:
- 임베딩 존재 여부 조회
- 도서 목록 조회
- 임베딩 저장 upsert

- [ ] **Step 5: tier2_embedder.py에 retry 적용**

tier1과 동일한 패턴으로 import + `with_retry(lambda: ...)` 적용.

- [ ] **Step 6: 전체 테스트 실행**

Run: `cd scripts && python -m pytest tests/ -v`
Expected: 모든 테스트 통과

- [ ] **Step 7: Commit**

```bash
git add scripts/lib/state_manager.py scripts/smart_batch_collector.py scripts/batch_enricher.py scripts/tier1_embedder.py scripts/tier2_embedder.py
git commit -m "fix: 모든 Supabase 호출에 retry wrapper 적용"
```

---

### Task 4: requirements.txt 업데이트

**Files:**
- Modify: `scripts/requirements.txt`

- [ ] **Step 1: 의존성 추가**

`scripts/requirements.txt`에 추가:
```
requests>=2.31.0
beautifulsoup4>=4.12.0
```

- [ ] **Step 2: Commit**

```bash
git add scripts/requirements.txt
git commit -m "chore: requests + beautifulsoup4 의존성 추가"
```

---

### Task 5: GitHub Actions 워크플로우 재편

**Files:**
- Rewrite: `.github/workflows/daily-batch.yml` (→ daily-collect로 역할 확장)
- Delete: `.github/workflows/daily-enrich.yml`
- Create: `.github/workflows/daily-scrape.yml`
- Create: `.github/workflows/daily-embed-t2.yml`

- [ ] **Step 1: daily-batch.yml → daily-collect.yml 리네임 + 재작성**

```bash
git mv .github/workflows/daily-batch.yml .github/workflows/daily-collect.yml
```

`.github/workflows/daily-collect.yml` 전체 교체:

```yaml
name: Daily Collect (Batch + Enrich + Tier 1 Embed)

on:
  schedule:
    - cron: '0 18 * * *'  # UTC 18:00 = KST 03:00
  workflow_dispatch:

jobs:
  collect-enrich-embed:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Run batch collector (target 1000 new books)
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          ALADIN_TTB_KEY: ${{ secrets.ALADIN_TTB_KEY }}
        run: python scripts/smart_batch_collector.py --daily-target 1000

      - name: Run batch enricher (color + font for new books)
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/batch_enricher.py --limit 500

      - name: Run Tier 1 embedder
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python scripts/tier1_embedder.py

      - name: Show status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          ALADIN_TTB_KEY: ${{ secrets.ALADIN_TTB_KEY }}
        run: |
          python scripts/smart_batch_collector.py --status
          python scripts/batch_enricher.py --status
```

- [ ] **Step 2: daily-scrape.yml 생성**

`.github/workflows/daily-scrape.yml`:

```yaml
name: Daily Scrape (YES24 Rich Descriptions)

on:
  schedule:
    - cron: '7 */2 * * *'  # 2시간마다 (매 :07분)
  workflow_dispatch:
    inputs:
      limit:
        description: '처리할 최대 권수'
        default: '80'

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Run YES24 scraper
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/yes24_scraper.py --limit ${{ github.event.inputs.limit || '80' }}

      - name: Show status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/yes24_scraper.py --status
```

- [ ] **Step 3: daily-embed-t2.yml 생성**

`.github/workflows/daily-embed-t2.yml`:

```yaml
name: Daily Tier 2 Embedding

on:
  schedule:
    - cron: '30 21 * * *'  # UTC 21:30 = KST 06:30
  workflow_dispatch:

jobs:
  embed-tier2:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Run Tier 2 embedder
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python scripts/tier2_embedder.py --limit 500

      - name: Show status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/tier2_embedder.py --status
```

- [ ] **Step 4: daily-enrich.yml 삭제**

```bash
git rm .github/workflows/daily-enrich.yml
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/
git commit -m "ci: 파이프라인 워크플로우 3개로 재편 — collect/scrape/embed-t2 분리"
```

---

### Task 6: ARCHITECTURE.md 업데이트

**Files:**
- Modify: `docs/ARCHITECTURE.md:65-72`

- [ ] **Step 1: 파이프라인 섹션 업데이트**

`docs/ARCHITECTURE.md` 65-72행을 다음으로 교체:

```
[MVP - Phase 1, 백그라운드 — GitHub Actions 자동화]
매일 KST 03:00 (daily-collect):
  → 알라딘 배치 수집 (베스트셀러/신간/저자/키워드 → books 테이블)
  → 색상 추출 + 폰트 배정 (cover → dominant_colors, spine_font)
  → Tier 1 임베딩 생성 (title+author+genre+description → book_embeddings)
2시간마다 (daily-scrape):
  → YES24 상세 수집 80권/회 (책소개/출판사리뷰/책속으로 → rich_description)
매일 KST 06:30 (daily-embed-t2):
  → Tier 2 임베딩 생성 (rich_description 기반 → book_embeddings 업그레이드)
```

- [ ] **Step 2: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: ARCHITECTURE.md 파이프라인 섹션을 3-워크플로우 구조로 업데이트"
```

---

### Task 7: 전체 검증

- [ ] **Step 1: 전체 테스트 실행**

Run: `cd scripts && python -m pytest tests/ -v`
Expected: 모든 테스트 통과

- [ ] **Step 2: YES24 스크래퍼 실제 dry-run (20권)**

Run: `cd scripts && python yes24_scraper.py --limit 20 --dry-run`
Expected: 성공률 ~90%, 에러 0건

- [ ] **Step 3: 스크래퍼 실제 실행 (10권, DB 저장)**

Run: `cd scripts && python yes24_scraper.py --limit 10`
Expected: 성공 8-10권, `--status`로 rich_description 증가 확인

- [ ] **Step 4: status 명령 전체 확인**

```bash
cd scripts
python smart_batch_collector.py --status
python batch_enricher.py --status
python yes24_scraper.py --status
python tier2_embedder.py --status
```

Expected: 모든 status 정상 출력, 에러 없음
