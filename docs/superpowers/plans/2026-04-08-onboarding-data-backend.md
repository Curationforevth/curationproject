# Onboarding Data + Backend Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 온보딩 Step 2 (책 선택) 의 히트율을 높이기 위해 정보나루를 책 DB의 메인 발견 소스로 격상하고, recommendation server에 `/similar/union` 추가, skip/cold start 용 fallback curation 시드.

**Architecture:** 정보나루 4개 endpoint (`loanItemSrch`, `recommandList`, `monthlyKeywords`, `srchBooks`) 를 통합한 discovery collector로 새 ISBN을 발견 → `addition_symbol[0] == '0'` (성인 단행본) 필터 → `dedup_checker` (제목+저자 정규화) 로 에디션 합침 → books upsert. 추천 서버는 selected book들의 desc 임베딩 평균을 받아 top-K 반환하는 `POST /similar/union` 추가. Fallback curation = 정보나루 인기 Top 30, 별도 테이블에 시드.

**Tech Stack:** Python 3 + requests + supabase-py (수집), FastAPI + numpy (서버), pytest + monkeypatch (테스트), Postgres (Supabase).

**참고 spec/메모:**
- `docs/superpowers/specs/2026-04-07-data-collection-design.md`
- `docs/superpowers/specs/2026-03-26-onboarding-design.md`
- 메모리 `project_data_priority.md` — 정보나루 최우선
- 메모리 `feedback_no_direct_sql.md` — SQL 적용은 Eden 수동
- 기존 enrich collector: `scripts/data4library_collector.py` — **건드리지 말 것**, 책별 키워드 보강용으로 운영 중
- 기존 dedup: `scripts/lib/dedup_checker.py` — 그대로 재사용
- recommendation server: `recommendation-server/main.py`, `api/similar.py`, `engine/index.py`

**검증된 사실 (실제 호출 결과 기반, 2026-04-08):**

1. `loanItemSrch` 응답 quality:
   - ISBN13 보유율 100%
   - KDC=8 (문학) top: 한강 - 소년이 온다 (대출 3,699) — 알라딘 베스트셀러와 명백히 다름
   - 응답 doc 키: `doc.bookname`, `doc.authors`, `doc.publisher`, `doc.isbn13`, `doc.addition_symbol`, `doc.class_no`, `doc.bookImageURL`, `doc.loan_count`, `doc.publication_year`

2. `recommandList` (ISBN 기반 similar):
   - 응답 doc 키 = `book` (loanItemSrch는 `doc`) — **dual-key handling 필수**
   - 결과 quality 매우 좋음: 소년이 온다(한강) → 채식주의자, 흰, 노랑무늬영원, 여수의 사랑 (한강 백카탈로그)
   - **신규 ISBN 발견 + similar 보강 양쪽에 활용 가능**

3. `addition_symbol` 필드:
   - 첫 자리 `0` = 단행본 (성인 일반), `7` = 아동, `5` = 청소년, `8` = 학습참고서
   - **KDC=4 (자연과학) 인기 대출은 20/20 모두 첫 자리 7 (어린이)** — 필터 없이 수집하면 어린이 책 풀이 됨
   - 필터: `addition_symbol[0] == '0'` 만 수집

4. dedup_checker 효과:
   - KDC=0 raw 20권 → 정규화 dedup 후 유니크 7권 (35% 절감)
   - 같은 작품의 페이퍼백/하드커버/세트 ISBN 분리 케이스를 잡음

5. `srchBooks` 한계:
   - "아몬드", "사피엔스", "한강" → 정상
   - "소년이 온다" (띄어쓰기) → **0 results** — token 매칭 실패
   - **단일 토큰 키워드만 사용 가능** (정확한 책 제목 검색은 카카오/알라딘에 의존)

6. `monthlyKeywords`:
   - 100개 키워드 + 가중치
   - 시즌 트렌드 즉시 반영 (2026-03: 사랑 48, 나태주 25, 인생, 마음, 풀꽃)
   - 단일 단어 위주 → srchBooks 호출 적합

---

## File Structure

**Create:**
- `scripts/lib/data4library_api.py` — 4 endpoints HTTP wrapper + 파싱 (재사용 가능, 테스트 가능)
- `scripts/data4library_discovery_collector.py` — Tier 1+2+3 통합 수집기 (신규 ISBN 발견)
- `tests/test_data4library_api.py` — wrapper 단위 테스트
- `tests/test_data4library_discovery.py` — 수집기 dedup/필터/dry-run 테스트
- `supabase/migrations/20260408_books_loan_count.sql` — books.loan_count 컬럼
- `supabase/migrations/20260408_fallback_curation.sql` — fallback_curation 테이블
- `recommendation-server/tests/test_similar_by_vector.py` — VectorIndex 메서드 테스트
- `recommendation-server/tests/test_similar_union.py` — endpoint 통합 테스트
- `scripts/seed_fallback_curation.py` — fallback curation 시드 스크립트
- `tests/test_seed_fallback_curation.py` — 시드 단위 테스트

**Modify:**
- `recommendation-server/engine/index.py` — `similar_by_vector(query_vec, exclude_ids, limit)` 추가, `similar_by_desc` 는 wrapper로 단순화
- `recommendation-server/models.py` — `SimilarUnionRequest` 추가
- `recommendation-server/api/similar.py` — `POST /similar/union` 라우트 추가

**Do NOT modify:**
- `scripts/data4library_collector.py` (기존 enrich 수집기, 운영 중)
- `scripts/lib/dedup_checker.py` (그대로 재사용)
- `scripts/lib/title_cleaner.py`

---

## Phase A — 정보나루 통합 수집기

### Task 1: API wrapper 4 endpoints (TDD)

**Files:**
- Create: `scripts/lib/data4library_api.py`
- Create: `tests/test_data4library_api.py`

**Why:** HTTP 호출과 파싱을 collector에서 분리. 파싱은 순수 함수로 테스트, HTTP는 monkeypatch로 mock 가능.

- [ ] **Step 1: Failing tests**

```python
# tests/test_data4library_api.py
"""정보나루 4개 endpoint wrapper 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.lib.data4library_api import (
    parse_book_docs,
    parse_monthly_keywords,
    is_adult_general,
    build_loan_item_params,
    build_recommand_params,
    build_search_params,
    build_monthly_keywords_params,
)


# ----- parse_book_docs : dual-key (doc / book) -----

def test_parse_book_docs_handles_loan_item_format():
    response = {
        "response": {
            "docs": [
                {"doc": {
                    "no": 1, "ranking": "1",
                    "bookname": "소년이 온다 :한강 장편소설 ",
                    "authors": "지은이: 한강",
                    "publisher": "창비",
                    "publication_year": "2014",
                    "isbn13": "9788936434120",
                    "addition_symbol": "03810",
                    "class_no": "813.62",
                    "bookImageURL": "http://image.aladin.co.kr/cover.jpg",
                    "loan_count": "3699",
                }},
            ]
        }
    }
    books = parse_book_docs(response)
    assert len(books) == 1
    b = books[0]
    assert b["isbn13"] == "9788936434120"
    assert b["title"] == "소년이 온다 :한강 장편소설"
    assert b["author_raw"] == "지은이: 한강"
    assert b["publisher"] == "창비"
    assert b["addition_symbol"] == "03810"
    assert b["loan_count"] == 3699
    assert b["cover_url"] == "http://image.aladin.co.kr/cover.jpg"


def test_parse_book_docs_handles_recommand_format():
    # recommandList wraps each entry under 'book'
    response = {
        "response": {
            "docs": [
                {"book": {
                    "no": 1,
                    "bookname": "채식주의자:한강 연작소설",
                    "authors": "한강",
                    "publisher": "창비",
                    "isbn13": "9788936433598",
                    "addition_symbol": "",
                    "class_no": "813.6",
                    "bookImageURL": "https://example.com/cover.jpg",
                }},
            ]
        }
    }
    books = parse_book_docs(response)
    assert len(books) == 1
    b = books[0]
    assert b["isbn13"] == "9788936433598"
    assert b["title"] == "채식주의자:한강 연작소설"
    assert b["loan_count"] == 0


def test_parse_book_docs_skips_books_without_isbn():
    response = {"response": {"docs": [
        {"doc": {"bookname": "no isbn"}},
        {"doc": {"bookname": "ok", "isbn13": "1111111111111"}},
    ]}}
    books = parse_book_docs(response)
    assert len(books) == 1
    assert books[0]["isbn13"] == "1111111111111"


def test_parse_book_docs_handles_empty_response():
    assert parse_book_docs({}) == []
    assert parse_book_docs({"response": {}}) == []
    assert parse_book_docs({"response": {"docs": []}}) == []


# ----- is_adult_general filter -----

def test_is_adult_general_accepts_first_digit_zero():
    assert is_adult_general({"addition_symbol": "03810"}) is True
    assert is_adult_general({"addition_symbol": "01000"}) is True


def test_is_adult_general_rejects_children_and_youth():
    assert is_adult_general({"addition_symbol": "73810"}) is False
    assert is_adult_general({"addition_symbol": "53810"}) is False
    assert is_adult_general({"addition_symbol": "83810"}) is False


def test_is_adult_general_treats_missing_as_pass():
    # Empty addition_symbol shows up in recommandList responses; we pass it.
    assert is_adult_general({"addition_symbol": ""}) is True
    assert is_adult_general({}) is True


# ----- monthly keywords parsing -----

def test_parse_monthly_keywords_extracts_words_with_weight():
    response = {
        "response": {
            "keywords": [
                {"keyword": {"word": "사랑", "weight": "48.354"}},
                {"keyword": {"word": "나태주", "weight": "25.328"}},
            ]
        }
    }
    kws = parse_monthly_keywords(response)
    assert len(kws) == 2
    assert kws[0] == ("사랑", 48.354)
    assert kws[1] == ("나태주", 25.328)


def test_parse_monthly_keywords_handles_missing_weight():
    response = {"response": {"keywords": [{"keyword": {"word": "test"}}]}}
    kws = parse_monthly_keywords(response)
    assert len(kws) == 1
    assert kws[0] == ("test", 0.0)


def test_parse_monthly_keywords_empty():
    assert parse_monthly_keywords({}) == []
    assert parse_monthly_keywords({"response": {}}) == []


# ----- param builders -----

def test_build_loan_item_params_with_kdc():
    p = build_loan_item_params(
        api_key="abc", page_no=1, page_size=50,
        start_dt="2026-01-01", end_dt="2026-04-01", kdc="8",
    )
    assert p["authKey"] == "abc"
    assert p["format"] == "json"
    assert p["pageNo"] == 1
    assert p["pageSize"] == 50
    assert p["startDt"] == "2026-01-01"
    assert p["endDt"] == "2026-04-01"
    assert p["kdc"] == "8"


def test_build_loan_item_params_without_kdc():
    p = build_loan_item_params(
        api_key="abc", page_no=1, page_size=50,
        start_dt="2026-01-01", end_dt="2026-04-01",
    )
    assert "kdc" not in p


def test_build_recommand_params_requires_isbn13():
    p = build_recommand_params(api_key="abc", isbn13="9788936434120", page_size=10)
    assert p["authKey"] == "abc"
    assert p["isbn13"] == "9788936434120"
    assert p["pageSize"] == 10
    assert p["format"] == "json"


def test_build_search_params():
    p = build_search_params(api_key="abc", keyword="한강", page_no=1, page_size=10)
    assert p["authKey"] == "abc"
    assert p["keyword"] == "한강"
    assert p["pageNo"] == 1
    assert p["pageSize"] == 10


def test_build_monthly_keywords_params():
    p = build_monthly_keywords_params(api_key="abc", month="2026-03")
    assert p["authKey"] == "abc"
    assert p["month"] == "2026-03"
    assert p["format"] == "json"
```

- [ ] **Step 2: Run tests — verify failure**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python3 -m pytest tests/test_data4library_api.py -v
```
Expected: All FAIL — `scripts.lib.data4library_api` does not exist.

- [ ] **Step 3: Implement wrapper**

Create `scripts/lib/data4library_api.py`:

```python
"""정보나루 (data4library) API wrapper.

Pure parsing functions + thin HTTP layer. The HTTP layer is intentionally
small so it can be mocked or replaced in tests/dry runs.

Endpoints supported:
  - loanItemSrch    : 인기 대출 도서 검색 (KDC × 기간) — 메인 발견 소스
  - recommandList   : 특정 ISBN과 비슷한 책 (백카탈로그/연관작)
  - srchBooks       : 키워드 검색 (단일 토큰만 안정)
  - monthlyKeywords : 월별 인기 키워드 (시드 확장용)

Response shape note: loanItemSrch wraps each result under `doc`,
recommandList wraps under `book`. parse_book_docs handles both.
"""
from __future__ import annotations

import re
from typing import Optional

import requests


API_BASE = "http://data4library.kr/api"


# ----- param builders -----

def build_loan_item_params(
    api_key: str, page_no: int, page_size: int,
    start_dt: str, end_dt: str, kdc: Optional[str] = None,
) -> dict:
    p = {
        "authKey": api_key,
        "format": "json",
        "pageNo": page_no,
        "pageSize": page_size,
        "startDt": start_dt,
        "endDt": end_dt,
    }
    if kdc:
        p["kdc"] = kdc
    return p


def build_recommand_params(api_key: str, isbn13: str, page_size: int = 10) -> dict:
    return {
        "authKey": api_key,
        "format": "json",
        "isbn13": isbn13,
        "pageNo": 1,
        "pageSize": page_size,
    }


def build_search_params(
    api_key: str, keyword: str, page_no: int = 1, page_size: int = 10,
) -> dict:
    return {
        "authKey": api_key,
        "format": "json",
        "keyword": keyword,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def build_monthly_keywords_params(api_key: str, month: str) -> dict:
    """month: 'YYYY-MM'"""
    return {
        "authKey": api_key,
        "format": "json",
        "month": month,
    }


# ----- parsing -----

def _clean_title(raw: str) -> str:
    """Strip whitespace; collapse internal whitespace.

    We keep the colon-style subtitle intact because dedup_checker normalizes
    it later.
    """
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def parse_book_docs(response: dict) -> list[dict]:
    """Parse a books-list response (loanItemSrch OR recommandList).

    Both endpoints return docs as a list under response.docs, but each item
    is wrapped under either `doc` (loanItemSrch) or `book` (recommandList).
    Books without isbn13 are skipped.

    Returns rows with normalized field names ready for downstream filtering.
    """
    if not response:
        return []
    docs = response.get("response", {}).get("docs", [])
    out: list[dict] = []
    for item in docs:
        d = item.get("doc") or item.get("book") or item
        isbn = (d.get("isbn13") or "").strip()
        if not isbn:
            continue
        loan_raw = d.get("loan_count") or "0"
        try:
            loan_count = int(loan_raw)
        except (TypeError, ValueError):
            loan_count = 0
        out.append({
            "isbn13": isbn,
            "title": _clean_title(d.get("bookname") or ""),
            "author_raw": (d.get("authors") or "").strip(),
            "publisher": (d.get("publisher") or "").strip() or None,
            "publication_year": (d.get("publication_year") or "").strip() or None,
            "addition_symbol": (d.get("addition_symbol") or "").strip(),
            "kdc": (d.get("class_no") or "").strip() or None,
            "cover_url": (d.get("bookImageURL") or "").strip() or None,
            "loan_count": loan_count,
        })
    return out


def parse_monthly_keywords(response: dict) -> list[tuple[str, float]]:
    """Return [(word, weight), ...] from monthlyKeywords response."""
    if not response:
        return []
    kws = response.get("response", {}).get("keywords", [])
    out: list[tuple[str, float]] = []
    for item in kws:
        kw = item.get("keyword") or {}
        word = kw.get("word") if isinstance(kw, dict) else None
        if not word:
            continue
        try:
            weight = float(kw.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        out.append((word, weight))
    return out


def is_adult_general(book: dict) -> bool:
    """Adult general filter: addition_symbol[0] == '0'.

    First digit meanings:
      0 = 단행본 (성인 일반)  ← target
      5 = 청소년
      6 = 대학
      7 = 아동
      8 = 학습참고서
      9 = 만화

    Empty addition_symbol → pass (recommandList often returns empty;
    we don't want to throw away the whole pipe).
    """
    sym = (book.get("addition_symbol") or "").strip()
    if not sym:
        return True
    return sym[0] == "0"


# ----- HTTP layer -----

def fetch_loan_item_page(
    api_key: str, page_no: int, page_size: int,
    start_dt: str, end_dt: str, kdc: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    params = build_loan_item_params(api_key, page_no, page_size, start_dt, end_dt, kdc)
    r = requests.get(f"{API_BASE}/loanItemSrch", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_recommand(api_key: str, isbn13: str, page_size: int = 10,
                    timeout: float = 60.0) -> dict:
    params = build_recommand_params(api_key, isbn13, page_size)
    r = requests.get(f"{API_BASE}/recommandList", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_search(api_key: str, keyword: str, page_no: int = 1, page_size: int = 10,
                 timeout: float = 60.0) -> dict:
    params = build_search_params(api_key, keyword, page_no, page_size)
    r = requests.get(f"{API_BASE}/srchBooks", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_monthly_keywords(api_key: str, month: str, timeout: float = 60.0) -> dict:
    params = build_monthly_keywords_params(api_key, month)
    r = requests.get(f"{API_BASE}/monthlyKeywords", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 4: Verify pass**

Run:
```bash
python3 -m pytest tests/test_data4library_api.py -v
```
Expected: All ~16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/data4library_api.py tests/test_data4library_api.py
git commit -m "feat: 정보나루 4개 endpoint wrapper + 파싱/필터 + 테스트"
```

---

### Task 2: books.loan_count migration (file only)

**Files:**
- Create: `supabase/migrations/20260408_books_loan_count.sql`

**Why:** 정보나루 인기대출 카운트를 books에 저장. sales_point 와 별개.

- [ ] **Step 1: Create migration file**

```sql
-- 20260408_books_loan_count.sql
-- 정보나루 인기대출 카운트를 books에 저장 (sales_point와 별개)
BEGIN;

ALTER TABLE public.books
  ADD COLUMN IF NOT EXISTS loan_count INT;

CREATE INDEX IF NOT EXISTS idx_books_loan_count
  ON public.books (loan_count DESC NULLS LAST);

COMMIT;
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/20260408_books_loan_count.sql
git commit -m "feat: books.loan_count 컬럼 (정보나루 인기대출)"
```

⚠️ **Eden 수동 적용 필요** — Supabase SQL Editor에서 실행. 메모리 `feedback_no_direct_sql.md` 참조.

---

### Task 3: Discovery collector — Tier 1 (loanItemSrch + KDC × 기간 + 필터 + dedup)

**Files:**
- Create: `scripts/data4library_discovery_collector.py`
- Create: `tests/test_data4library_discovery.py`

**Why:** 정보나루 인기대출이 메인 신규 ISBN 발견 소스. KDC 0~9 × 다중 페이지 + 어린이 필터 + dedup_checker.

- [ ] **Step 1: Failing tests for pure logic**

```python
# tests/test_data4library_discovery.py
"""Discovery collector — pure logic 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.data4library_discovery_collector import (
    KDC_BUCKETS,
    dedup_in_batch_by_isbn,
    sanitize_for_upsert,
    extract_first_author,
)


def test_kdc_buckets_cover_main_genres():
    keys = {b["kdc"] for b in KDC_BUCKETS}
    assert "8" in keys  # 문학
    assert "1" in keys  # 철학
    assert "3" in keys  # 사회과학
    assert "9" in keys  # 역사


def test_dedup_in_batch_by_isbn_keeps_highest_loan_count():
    rows = [
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 100},
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 250},
        {"isbn13": "9788954682152", "title": "작별하지 않는다", "loan_count": 200},
    ]
    out = dedup_in_batch_by_isbn(rows)
    assert len(out) == 2
    by_isbn = {r["isbn13"]: r for r in out}
    assert by_isbn["9788936434120"]["loan_count"] == 250
    assert by_isbn["9788954682152"]["loan_count"] == 200


def test_extract_first_author_strips_role_prefix():
    assert extract_first_author("지은이: 한강") == "한강"
    assert extract_first_author("저자: 유발 하라리 ;옮긴이: 조현욱") == "유발 하라리"
    assert extract_first_author("글: 최설희 ;그림: 한현동") == "최설희"
    assert extract_first_author("한강") == "한강"
    assert extract_first_author("") == ""
    assert extract_first_author(None) == ""


def test_sanitize_for_upsert_maps_columns():
    parsed = {
        "isbn13": "9788936434120",
        "title": "소년이 온다 :한강 장편소설",
        "author_raw": "지은이: 한강",
        "publisher": "창비",
        "publication_year": "2014",
        "addition_symbol": "03810",
        "kdc": "813.62",
        "cover_url": "http://example.com/cover.jpg",
        "loan_count": 3699,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9788936434120"
    assert row["title"] == "소년이 온다 :한강 장편소설"
    assert row["author"] == "한강"
    assert row["publisher"] == "창비"
    assert row["cover_url"] == "http://example.com/cover.jpg"
    assert row["loan_count"] == 3699
    assert row["sales_point"] == 3699
    assert "isbn13" not in row
    assert "kdc" not in row
    assert "addition_symbol" not in row
    assert "publication_year" not in row
    assert "author_raw" not in row


def test_sanitize_for_upsert_handles_missing_optional():
    parsed = {
        "isbn13": "9999999999999",
        "title": "x",
        "author_raw": "",
        "publisher": None,
        "publication_year": None,
        "addition_symbol": "",
        "kdc": None,
        "cover_url": None,
        "loan_count": 0,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9999999999999"
    assert row["author"] == ""
    assert row["loan_count"] == 0
```

- [ ] **Step 2: Verify failing**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement collector (Tier 1 only)**

Create `scripts/data4library_discovery_collector.py`:

```python
"""정보나루 신규 ISBN 발견 수집기 (discovery).

기존 `data4library_collector.py` (책별 키워드 enrich) 와 별개. 이 스크립트는
새 책을 발견해서 books 테이블에 채우는 게 목적.

Tier 1: loanItemSrch — KDC × 기간 인기 대출 (주 발견 소스)
Tier 2: recommandList — Tier 1 결과의 백카탈로그/연관작 (Task 4)
Tier 3: monthlyKeywords + srchBooks — 트렌드 키워드 확장 (Task 5)

성인 단행본만 수집 (addition_symbol[0] == '0').
에디션 중복은 dedup_checker로 제거.

사용법:
  python3 scripts/data4library_discovery_collector.py --tier 1 --dry-run
  python3 scripts/data4library_discovery_collector.py --tier 1
  python3 scripts/data4library_discovery_collector.py --tier 1 --period-days 180 --pages 2
  python3 scripts/data4library_discovery_collector.py --status
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.lib.data4library_api import (
    fetch_loan_item_page,
    parse_book_docs,
    is_adult_general,
)
from scripts.lib.dedup_checker import DeduplicateChecker

load_dotenv(os.path.join(REPO, ".env"))


PAGE_SIZE = 50
REQUEST_DELAY = 0.5


KDC_BUCKETS = [
    {"kdc": "0", "label": "총류"},
    {"kdc": "1", "label": "철학"},
    {"kdc": "2", "label": "종교"},
    {"kdc": "3", "label": "사회과학"},
    {"kdc": "4", "label": "자연과학"},
    {"kdc": "5", "label": "기술과학"},
    {"kdc": "6", "label": "예술"},
    {"kdc": "7", "label": "언어"},
    {"kdc": "8", "label": "문학"},
    {"kdc": "9", "label": "역사"},
]


def dedup_in_batch_by_isbn(rows: list[dict]) -> list[dict]:
    """In-batch ISBN dedup. Keeps the row with the highest loan_count."""
    by_isbn: dict[str, dict] = {}
    for r in rows:
        isbn = (r.get("isbn13") or "").strip()
        if not isbn:
            continue
        existing = by_isbn.get(isbn)
        if existing is None or (r.get("loan_count") or 0) > (existing.get("loan_count") or 0):
            by_isbn[isbn] = r
    return list(by_isbn.values())


def extract_first_author(author_raw: Optional[str]) -> str:
    """'지은이: 유발 하라리 ;옮긴이: 조현욱' -> '유발 하라리'."""
    if not author_raw:
        return ""
    s = re.sub(r"^(지은이|저자|글|원작)\s*[:：]\s*", "", author_raw)
    s = re.split(r"[;,]", s)[0]
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_for_upsert(parsed: dict) -> dict:
    """Convert a parsed loanItem dict to a books-table row."""
    return {
        "isbn": parsed["isbn13"],
        "title": parsed.get("title") or "",
        "author": extract_first_author(parsed.get("author_raw")),
        "publisher": parsed.get("publisher"),
        "cover_url": parsed.get("cover_url"),
        "loan_count": parsed.get("loan_count") or 0,
        "sales_point": parsed.get("loan_count") or 0,
    }


class DiscoveryCollector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key: Optional[str] = None
        self._sb = None
        self._dedup: Optional[DeduplicateChecker] = None
        self.stats = {
            "fetched_raw": 0,
            "filtered_children": 0,
            "filtered_isbn_dup": 0,
            "filtered_edition_dup": 0,
            "upserted": 0,
            "errors": 0,
        }

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = os.getenv("DATA4LIBRARY_API_KEY")
            if not self._api_key:
                print("ERROR: DATA4LIBRARY_API_KEY not set", file=sys.stderr)
                sys.exit(1)
        return self._api_key

    @property
    def sb(self):
        if self._sb is None:
            from supabase import create_client
            self._sb = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            )
        return self._sb

    @property
    def dedup(self) -> DeduplicateChecker:
        if self._dedup is None:
            print("📚 dedup index 로딩 중...")
            self._dedup = DeduplicateChecker(self.sb)
            count = self._dedup.load_title_index()
            print(f"   {count}권 인덱스 완료")
        return self._dedup

    def fetch_tier1(self, period_days: int = 30, pages: int = 1) -> list[dict]:
        """Tier 1: loanItemSrch × KDC 0~9 × pages."""
        end = datetime.now().date()
        start = end - timedelta(days=period_days)
        start_dt = start.strftime("%Y-%m-%d")
        end_dt = end.strftime("%Y-%m-%d")

        all_rows: list[dict] = []
        for bucket in KDC_BUCKETS:
            for page in range(1, pages + 1):
                try:
                    raw = fetch_loan_item_page(
                        api_key=self.api_key,
                        page_no=page, page_size=PAGE_SIZE,
                        start_dt=start_dt, end_dt=end_dt,
                        kdc=bucket["kdc"],
                    )
                    parsed = parse_book_docs(raw)
                    print(f"  [KDC {bucket['kdc']} {bucket['label']}] page {page}: {len(parsed)} books")
                    all_rows.extend(parsed)
                    self.stats["fetched_raw"] += len(parsed)
                except Exception as e:
                    print(f"  ✗ [KDC {bucket['kdc']}] page {page}: {e}")
                    self.stats["errors"] += 1
                time.sleep(REQUEST_DELAY)
        return all_rows

    def filter_and_upsert(self, parsed_rows: list[dict]) -> int:
        """Apply children filter, batch ISBN dedup, edition dedup, then upsert."""
        adult_rows = [r for r in parsed_rows if is_adult_general(r)]
        self.stats["filtered_children"] = len(parsed_rows) - len(adult_rows)
        print(f"  성인 단행본 필터: {len(adult_rows)}/{len(parsed_rows)}")

        by_isbn = dedup_in_batch_by_isbn(adult_rows)
        self.stats["filtered_isbn_dup"] = len(adult_rows) - len(by_isbn)
        print(f"  배치 ISBN dedup: {len(by_isbn)}/{len(adult_rows)}")

        if not by_isbn:
            return 0

        unique_rows: list[dict] = []
        for r in by_isbn:
            title = r.get("title") or ""
            author = extract_first_author(r.get("author_raw"))
            isbn = r["isbn13"]
            if self.dedup.is_title_duplicate(title, author, isbn):
                self.stats["filtered_edition_dup"] += 1
                continue
            unique_rows.append(r)
            self.dedup.register(title, author, isbn)
        print(f"  에디션 dedup: {len(unique_rows)}/{len(by_isbn)}")

        if not unique_rows:
            return 0

        rows = [sanitize_for_upsert(r) for r in unique_rows]
        if self.dry_run:
            print(f"  (dry-run) would upsert {len(rows)} rows")
            print(f"  sample: {rows[0]}")
            return len(rows)

        upserted = 0
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            self.sb.table("books").upsert(chunk, on_conflict="isbn").execute()
            upserted += len(chunk)
        self.stats["upserted"] = upserted
        return upserted

    def show_status(self):
        total = self.sb.table("books").select("id", count="exact").execute()
        with_loan = (
            self.sb.table("books")
            .select("id", count="exact")
            .not_.is_("loan_count", "null")
            .execute()
        )
        print(f"books: {total.count}, with loan_count: {with_loan.count}")

    def report(self):
        print("\n=== discovery collector 결과 ===")
        for k, v in self.stats.items():
            print(f"  {k}: {v}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--period-days", type=int, default=30)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--status", action="store_true")
    args = p.parse_args()

    c = DiscoveryCollector(dry_run=args.dry_run)
    if args.status:
        c.show_status()
        return

    if args.tier == 1:
        print(f"Tier 1: loanItemSrch × {len(KDC_BUCKETS)} KDC × {args.pages} pages × {PAGE_SIZE}/page")
        rows = c.fetch_tier1(args.period_days, args.pages)
        c.filter_and_upsert(rows)
    else:
        print(f"Tier {args.tier} 는 다음 task 에서 구현됩니다.")
    c.report()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Dry-run on real API**

Run:
```bash
python3 scripts/data4library_discovery_collector.py --tier 1 --dry-run --pages 1
```
Expected:
- 10 KDC × 1 page 호출
- raw fetched ~500 (50 × 10)
- 성인 단행본 필터 후 ~150-250 (어린이가 많은 KDC들에서 줄어듦)
- 배치 ISBN dedup 후 더 줄어듦
- 에디션 dedup 후 unique 갯수 출력
- "would upsert N rows" + sample
- errors 0

- [ ] **Step 6: Commit**

```bash
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "feat: 정보나루 discovery collector Tier 1 (loanItemSrch + 어린이 필터 + dedup)"
```

---

### Task 4: Discovery collector — Tier 2 (recommandList 백카탈로그)

**Files:**
- Modify: `scripts/data4library_discovery_collector.py`
- Modify: `tests/test_data4library_discovery.py`

**Why:** Tier 1으로 발견된 인기 책의 ISBN으로 recommandList 호출 → 같은 작가/연관작 자동 확장. 검증 결과: 소년이 온다 → 한강 백카탈로그 4권 자동 발견.

- [ ] **Step 1: Failing test for selection logic**

Append to `tests/test_data4library_discovery.py`:

```python
from scripts.data4library_discovery_collector import (
    select_seed_isbns_for_tier2,
)


def test_select_seed_isbns_for_tier2_picks_top_n_by_loan_count():
    rows = [
        {"isbn13": "isbn1", "loan_count": 500},
        {"isbn13": "isbn2", "loan_count": 1500},
        {"isbn13": "isbn3", "loan_count": 100},
        {"isbn13": "isbn4", "loan_count": 800},
    ]
    seeds = select_seed_isbns_for_tier2(rows, top_n=2)
    assert seeds == ["isbn2", "isbn4"]


def test_select_seed_isbns_for_tier2_skips_blank_isbn():
    rows = [
        {"isbn13": "", "loan_count": 9999},
        {"isbn13": "isbn2", "loan_count": 100},
    ]
    seeds = select_seed_isbns_for_tier2(rows, top_n=5)
    assert seeds == ["isbn2"]


def test_select_seed_isbns_for_tier2_empty():
    assert select_seed_isbns_for_tier2([], top_n=10) == []
```

- [ ] **Step 2: Verify failing**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py::test_select_seed_isbns_for_tier2_picks_top_n_by_loan_count -v
```
Expected: ImportError.

- [ ] **Step 3: Add Tier 2 to collector**

In `scripts/data4library_discovery_collector.py`, add this function (after `extract_first_author`):

```python
def select_seed_isbns_for_tier2(rows: list[dict], top_n: int = 50) -> list[str]:
    """Select top-N ISBN seeds for Tier 2 recommandList expansion.

    Sort by loan_count desc, take ISBN13s, drop blanks, dedupe in order.
    """
    sorted_rows = sorted(rows, key=lambda r: r.get("loan_count") or 0, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for r in sorted_rows:
        isbn = (r.get("isbn13") or "").strip()
        if not isbn or isbn in seen:
            continue
        seen.add(isbn)
        out.append(isbn)
        if len(out) >= top_n:
            break
    return out
```

Add a Tier 2 method to `DiscoveryCollector`:

```python
    def fetch_tier2(self, seed_isbns: list[str]) -> list[dict]:
        """Tier 2: recommandList for each seed ISBN."""
        from scripts.lib.data4library_api import fetch_recommand
        all_rows: list[dict] = []
        for i, isbn in enumerate(seed_isbns):
            try:
                raw = fetch_recommand(api_key=self.api_key, isbn13=isbn, page_size=10)
                parsed = parse_book_docs(raw)
                all_rows.extend(parsed)
                self.stats["fetched_raw"] += len(parsed)
                if (i + 1) % 10 == 0:
                    print(f"  recommandList progress: {i+1}/{len(seed_isbns)}")
            except Exception as e:
                print(f"  ✗ recommandList isbn={isbn}: {e}")
                self.stats["errors"] += 1
            time.sleep(REQUEST_DELAY)
        return all_rows
```

Update `main()` to handle `--tier 2`:

```python
    if args.tier == 1:
        print(f"Tier 1: loanItemSrch × {len(KDC_BUCKETS)} KDC × {args.pages} pages × {PAGE_SIZE}/page")
        rows = c.fetch_tier1(args.period_days, args.pages)
        c.filter_and_upsert(rows)
    elif args.tier == 2:
        print(f"Tier 2: recommandList for top-{args.tier2_seeds} books from Tier 1 result")
        tier1_rows = c.fetch_tier1(args.period_days, args.pages)
        seeds = select_seed_isbns_for_tier2(tier1_rows, top_n=args.tier2_seeds)
        print(f"  selected {len(seeds)} seed ISBNs")
        tier2_rows = c.fetch_tier2(seeds)
        c.filter_and_upsert(tier1_rows + tier2_rows)
    else:
        print(f"Tier {args.tier} 는 다음 task 에서 구현됩니다.")
```

Add the new arg:

```python
    p.add_argument("--tier2-seeds", type=int, default=50,
                   help="Tier 2: how many top seed ISBNs to expand via recommandList")
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py -v
```
Expected: 8 tests PASS.

- [ ] **Step 5: Dry-run Tier 2**

Run:
```bash
python3 scripts/data4library_discovery_collector.py --tier 2 --dry-run --tier2-seeds 10
```
Expected:
- Tier 1 fetch (10 KDC) 완료
- Selected 10 seed ISBNs
- recommandList 10번 호출
- 최종 filter_and_upsert 가 Tier 1+2 합쳐서 처리
- "(dry-run) would upsert N rows"

- [ ] **Step 6: Commit**

```bash
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "feat: discovery collector Tier 2 — recommandList 백카탈로그 확장"
```

---

### Task 5: Discovery collector — Tier 3 (monthlyKeywords + srchBooks 트렌드)

**Files:**
- Modify: `scripts/data4library_discovery_collector.py`
- Modify: `tests/test_data4library_discovery.py`

**Why:** monthlyKeywords 100개에서 단일 토큰만 골라 srchBooks 호출 → 시즌 트렌드 자동 발견.

- [ ] **Step 1: Failing test**

Append to `tests/test_data4library_discovery.py`:

```python
from scripts.data4library_discovery_collector import (
    filter_single_token_keywords,
)


def test_filter_single_token_keywords_keeps_single_words():
    keywords = [
        ("사랑", 48.354),
        ("나태주 시집", 25.328),
        ("인생", 25.328),
        ("풀꽃", 20.723),
    ]
    out = filter_single_token_keywords(keywords)
    assert ("사랑", 48.354) in out
    assert ("인생", 25.328) in out
    assert ("풀꽃", 20.723) in out
    assert all(" " not in w for w, _ in out)
    assert len(out) == 3


def test_filter_single_token_keywords_drops_too_short_words():
    keywords = [("사", 99.0), ("사랑", 50.0)]
    out = filter_single_token_keywords(keywords)
    assert ("사", 99.0) not in out
    assert ("사랑", 50.0) in out


def test_filter_single_token_keywords_dedupes():
    keywords = [("사랑", 50.0), ("사랑", 30.0)]
    out = filter_single_token_keywords(keywords)
    assert len(out) == 1
    assert out[0][0] == "사랑"
```

- [ ] **Step 2: Verify failing**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py -k single_token -v
```
Expected: ImportError.

- [ ] **Step 3: Add Tier 3 logic + method**

In `scripts/data4library_discovery_collector.py`, add this helper:

```python
def filter_single_token_keywords(keywords: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep only single-word keywords with len >= 2 (srchBooks limitation).

    Verified: srchBooks fails on multi-token keywords like '소년이 온다'.
    Dedupes by word (keeps first occurrence).
    """
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for word, weight in keywords:
        if not word or len(word) < 2 or " " in word:
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append((word, weight))
    return out
```

Add Tier 3 method to `DiscoveryCollector`:

```python
    def fetch_tier3(self, month: str) -> list[dict]:
        """Tier 3: monthlyKeywords -> filter single tokens -> srchBooks."""
        from scripts.lib.data4library_api import (
            fetch_monthly_keywords, parse_monthly_keywords, fetch_search,
        )
        try:
            kw_raw = fetch_monthly_keywords(api_key=self.api_key, month=month)
        except Exception as e:
            print(f"  ✗ monthlyKeywords {month}: {e}")
            self.stats["errors"] += 1
            return []
        keywords = parse_monthly_keywords(kw_raw)
        single = filter_single_token_keywords(keywords)
        print(f"  monthlyKeywords {month}: {len(keywords)} 키워드 → 단일 토큰 {len(single)}")

        all_rows: list[dict] = []
        for i, (word, _weight) in enumerate(single):
            try:
                raw = fetch_search(api_key=self.api_key, keyword=word, page_size=10)
                parsed = parse_book_docs(raw)
                all_rows.extend(parsed)
                self.stats["fetched_raw"] += len(parsed)
                if (i + 1) % 20 == 0:
                    print(f"  srchBooks progress: {i+1}/{len(single)}")
            except Exception as e:
                print(f"  ✗ srchBooks '{word}': {e}")
                self.stats["errors"] += 1
            time.sleep(REQUEST_DELAY)
        return all_rows
```

Update `main()`:

```python
    elif args.tier == 3:
        if args.month:
            month = args.month
        else:
            today = datetime.now().date()
            mm = today.month - 1 if today.month > 1 else 12
            yyyy = today.year if today.month > 1 else today.year - 1
            month = f"{yyyy}-{mm:02d}"
        print(f"Tier 3: monthlyKeywords month={month} → srchBooks")
        rows = c.fetch_tier3(month)
        c.filter_and_upsert(rows)
```

Add arg:

```python
    p.add_argument("--month", type=str, default=None,
                   help="Tier 3 month (YYYY-MM). Default = previous month")
```

- [ ] **Step 4: Verify tests pass**

Run:
```bash
python3 -m pytest tests/test_data4library_discovery.py -v
```
Expected: 11 tests PASS.

- [ ] **Step 5: Dry-run Tier 3**

Run:
```bash
python3 scripts/data4library_discovery_collector.py --tier 3 --dry-run
```
Expected: monthlyKeywords 1번, ~50-80 단일 토큰, srchBooks 호출 N번, dedup 후 sample.

- [ ] **Step 6: Commit**

```bash
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "feat: discovery collector Tier 3 — monthlyKeywords + srchBooks 트렌드"
```

---

## Phase B — Recommendation server `/similar/union`

### Task 6: VectorIndex.similar_by_vector (TDD)

**Files:**
- Modify: `recommendation-server/engine/index.py`
- Create: `recommendation-server/tests/test_similar_by_vector.py`

**Why:** Union endpoint이 임의 query 벡터를 받으려면 index가 그 기능을 노출해야 함.

- [ ] **Step 1: Failing test**

Create `recommendation-server/tests/test_similar_by_vector.py`:

```python
"""VectorIndex.similar_by_vector — 임의 query 벡터로 lookup."""
import numpy as np
import pytest
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def small_index():
    idx = VectorIndex(dim=8)
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    sci_l1 = _norm([0, 0, 1, 0, 0, 0, 0, 0])
    novel_l2a = _norm([1, 0, 0, 0, 0.3, 0, 0, 0])
    novel_l2b = _norm([1, 0, 0, 0, 0, 0.3, 0, 0])
    econ_l2 = _norm([0, 1, 0, 0, 0, 0, 0.3, 0])
    sci_l2 = _norm([0, 0, 1, 0, 0, 0, 0, 0.3])
    idx.add_book("novel1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                 l1=novel_l1, l2=novel_l2a, reasons=[])
    idx.add_book("novel2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l2b, reasons=[])
    idx.add_book("econ1", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l2, reasons=[])
    idx.add_book("econ2", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l2, reasons=[])
    idx.add_book("sci1", desc=_norm([0, 0, 1, 0, 0, 0, 0, 0.5]),
                 l1=sci_l1, l2=sci_l2, reasons=[])
    idx.build_desc_matrix()
    return idx


def test_similar_by_vector_returns_top_k_excluding_ids(small_index):
    nov1 = small_index.get_book("novel1").desc
    nov2 = small_index.get_book("novel2").desc
    avg = (nov1 + nov2) / 2
    avg = avg / np.linalg.norm(avg)
    results = small_index.similar_by_vector(avg, exclude_ids={"novel1", "novel2"}, limit=3)
    assert len(results) == 3
    ids = [r[0] for r in results]
    assert "novel1" not in ids
    assert "novel2" not in ids
    for _, score in results:
        assert -1.0 <= score <= 1.0


def test_similar_by_vector_orders_by_descending_score(small_index):
    nov1 = small_index.get_book("novel1").desc
    results = small_index.similar_by_vector(nov1, exclude_ids=set(), limit=5)
    scores = [r[1] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0][0] == "novel1"


def test_similar_by_vector_handles_empty_exclude(small_index):
    econ1 = small_index.get_book("econ1").desc
    results = small_index.similar_by_vector(econ1, exclude_ids=set(), limit=2)
    assert len(results) == 2


def test_similar_by_vector_respects_limit(small_index):
    nov1 = small_index.get_book("novel1").desc
    results = small_index.similar_by_vector(nov1, exclude_ids=set(), limit=1)
    assert len(results) == 1
```

- [ ] **Step 2: Verify failing**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation/recommendation-server"
python3 -m pytest tests/test_similar_by_vector.py -v
```
Expected: AttributeError.

- [ ] **Step 3: Implement**

In `recommendation-server/engine/index.py`, replace the `similar_by_desc` method (lines 52-62) with:

```python
    def similar_by_vector(
        self,
        query_vec: np.ndarray,
        exclude_ids: set[str] | None = None,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Score every book in the index against an arbitrary L2-normalized
        query vector and return top-K (book_id, score), excluding any ids
        in exclude_ids.
        """
        if self._desc_matrix is None:
            self.build_desc_matrix()
        if exclude_ids is None:
            exclude_ids = set()
        scores = self._desc_matrix @ query_vec.astype(self.dtype)
        for ex in exclude_ids:
            if ex in self._desc_bid_order:
                scores[self._desc_bid_order.index(ex)] = -999.0
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i])) for i in top_idx]

    def similar_by_desc(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        bv = self._books.get(book_id)
        if bv is None:
            return []
        return self.similar_by_vector(bv.desc, exclude_ids={book_id}, limit=limit)
```

- [ ] **Step 4: Run new + existing tests**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation/recommendation-server"
python3 -m pytest tests/test_similar_by_vector.py tests/test_index.py -v
```
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/index.py recommendation-server/tests/test_similar_by_vector.py
git commit -m "feat: VectorIndex.similar_by_vector — 임의 query 벡터 lookup"
```

---

### Task 7: POST /similar/union endpoint

**Files:**
- Modify: `recommendation-server/models.py`
- Modify: `recommendation-server/api/similar.py`
- Create: `recommendation-server/tests/test_similar_union.py`

**Why:** 클라이언트가 N권 선택하면 한 번의 round-trip으로 union recommendation을 받는다.

- [ ] **Step 1: Failing endpoint test**

Create `recommendation-server/tests/test_similar_union.py`:

```python
"""POST /similar/union — selected books의 평균 벡터로 top-K."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from main import app
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def client_with_index():
    idx = VectorIndex(dim=8)
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    idx.add_book("b1", desc=_norm([1, 0, 0, 0, 0.5, 0, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b3", desc=_norm([1, 0, 0, 0, 0, 0.9, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b4", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l1, reasons=[])
    idx.add_book("b5", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l1, reasons=[])
    idx.build_desc_matrix()

    app.state.index = idx
    app.state.books_meta = {
        "b1": {"title": "B1", "author": "A1", "cover_url": "u1"},
        "b2": {"title": "B2", "author": "A2", "cover_url": "u2"},
        "b3": {"title": "B3", "author": "A3", "cover_url": "u3"},
        "b4": {"title": "B4", "author": "A4", "cover_url": "u4"},
        "b5": {"title": "B5", "author": "A5", "cover_url": "u5"},
    }
    app.state.built_at = "test"
    return TestClient(app)


@patch("api.similar.verify_jwt", return_value="test-user")
def test_similar_union_returns_top_k_excluding_input(_, client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["b1", "b2"], "limit": 3},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "similar" in body
    ids = [s["book_id"] for s in body["similar"]]
    assert "b1" not in ids
    assert "b2" not in ids
    assert len(body["similar"]) == 3


@patch("api.similar.verify_jwt", return_value="test-user")
def test_similar_union_with_unknown_book_ids_skips_them(_, client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["b1", "doesnotexist"], "limit": 2},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    body = r.json()
    ids = [s["book_id"] for s in body["similar"]]
    assert "b1" not in ids
    assert "doesnotexist" not in ids
    assert len(body["similar"]) == 2


@patch("api.similar.verify_jwt", return_value="test-user")
def test_similar_union_empty_input_returns_empty(_, client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": [], "limit": 5},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    assert r.json()["similar"] == []


@patch("api.similar.verify_jwt", return_value="test-user")
def test_similar_union_all_unknown_returns_empty(_, client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["nope1", "nope2"], "limit": 5},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    assert r.json()["similar"] == []
```

- [ ] **Step 2: Verify failing**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation/recommendation-server"
python3 -m pytest tests/test_similar_union.py -v
```
Expected: 404.

- [ ] **Step 3: Add request model**

In `recommendation-server/models.py`, add at the bottom:

```python
class SimilarUnionRequest(BaseModel):
    book_ids: List[str]
    limit: int = 6
```

- [ ] **Step 4: Add /similar/union route**

Replace `recommendation-server/api/similar.py` contents with:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import numpy as np

from auth import verify_jwt
from models import SimilarResponse, SimilarBook, SimilarUnionRequest
from config import DEFAULT_SIMILAR_LIMIT

router = APIRouter()


def _build_similar_books(results, books_meta) -> list[SimilarBook]:
    out: list[SimilarBook] = []
    for bid, score in results:
        meta = books_meta.get(bid, {})
        out.append(SimilarBook(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""), author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        ))
    return out


@router.get("/similar/{book_id}", response_model=SimilarResponse)
async def get_similar(
    book_id: str,
    request: Request,
    limit: int = Query(DEFAULT_SIMILAR_LIMIT, ge=1, le=50),
    _: str = Depends(verify_jwt),
):
    index = request.app.state.index
    books_meta = request.app.state.books_meta

    if index.get_book(book_id) is None:
        raise HTTPException(404, f"Book {book_id} not found in index")

    results = index.similar_by_desc(book_id, limit=limit)
    return SimilarResponse(book_id=book_id, similar=_build_similar_books(results, books_meta))


@router.post("/similar/union", response_model=SimilarResponse)
async def similar_union(
    payload: SimilarUnionRequest,
    request: Request,
    _: str = Depends(verify_jwt),
):
    """Average the desc embeddings of the supplied book_ids and return
    top-K nearest books, excluding the inputs themselves.

    Books not present in the index are silently skipped.
    Returns an empty similar list if no input books exist in the index.
    """
    index = request.app.state.index
    books_meta = request.app.state.books_meta
    limit = max(1, min(50, payload.limit))

    vectors = []
    seed_ids = set()
    for bid in payload.book_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        vectors.append(bv.desc)
        seed_ids.add(bid)

    if not vectors:
        return SimilarResponse(book_id="union", similar=[])

    avg = np.mean(np.stack(vectors), axis=0)
    norm = float(np.linalg.norm(avg))
    if norm == 0:
        return SimilarResponse(book_id="union", similar=[])
    avg = avg / norm

    results = index.similar_by_vector(avg, exclude_ids=seed_ids, limit=limit)
    return SimilarResponse(book_id="union", similar=_build_similar_books(results, books_meta))
```

- [ ] **Step 5: Run all server tests**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation/recommendation-server"
python3 -m pytest tests/ -v
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add recommendation-server/api/similar.py \
        recommendation-server/models.py \
        recommendation-server/tests/test_similar_union.py
git commit -m "feat: POST /similar/union — N권 평균 벡터로 top-K"
```

---

## Phase C — Fallback curation (skip + cold start)

### Task 8: fallback_curation 테이블 + 시드 스크립트

**Files:**
- Create: `supabase/migrations/20260408_fallback_curation.sql`
- Create: `scripts/seed_fallback_curation.py`
- Create: `tests/test_seed_fallback_curation.py`

**Why:** Skip 유저 + 추천 서버 cold start fallback. 정보나루 인기 Top 30 정적 큐레이션.

- [ ] **Step 1: Migration file**

```sql
-- supabase/migrations/20260408_fallback_curation.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.fallback_curation (
  id BIGSERIAL PRIMARY KEY,
  rank INT NOT NULL,
  book_id UUID NOT NULL REFERENCES public.books(id) ON DELETE CASCADE,
  loan_count INT,
  added_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  UNIQUE (book_id)
);

CREATE INDEX IF NOT EXISTS idx_fallback_rank
  ON public.fallback_curation (rank ASC);

ALTER TABLE public.fallback_curation ENABLE ROW LEVEL SECURITY;

CREATE POLICY "모두 읽기" ON public.fallback_curation
  FOR SELECT USING (true);

COMMIT;
```

- [ ] **Step 2: Failing test**

Create `tests/test_seed_fallback_curation.py`:

```python
"""Fallback curation 시드 스크립트 — pure logic 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.seed_fallback_curation import (
    rank_books_by_loan_count,
    build_fallback_rows,
)


def test_rank_books_by_loan_count_descending():
    books = [
        {"id": "u1", "loan_count": 100},
        {"id": "u2", "loan_count": 500},
        {"id": "u3", "loan_count": 250},
    ]
    out = rank_books_by_loan_count(books)
    assert [b["id"] for b in out] == ["u2", "u3", "u1"]


def test_rank_books_by_loan_count_skips_null():
    books = [
        {"id": "u1", "loan_count": None},
        {"id": "u2", "loan_count": 100},
    ]
    out = rank_books_by_loan_count(books)
    assert len(out) == 1
    assert out[0]["id"] == "u2"


def test_build_fallback_rows_assigns_sequential_ranks():
    ranked = [
        {"id": "u2", "loan_count": 500},
        {"id": "u3", "loan_count": 250},
        {"id": "u1", "loan_count": 100},
    ]
    rows = build_fallback_rows(ranked)
    assert rows == [
        {"rank": 1, "book_id": "u2", "loan_count": 500},
        {"rank": 2, "book_id": "u3", "loan_count": 250},
        {"rank": 3, "book_id": "u1", "loan_count": 100},
    ]


def test_build_fallback_rows_truncates_to_limit():
    ranked = [{"id": f"u{i}", "loan_count": 100 - i} for i in range(50)]
    rows = build_fallback_rows(ranked, limit=30)
    assert len(rows) == 30
    assert rows[0]["rank"] == 1
    assert rows[-1]["rank"] == 30
```

- [ ] **Step 3: Verify failing**

Run:
```bash
python3 -m pytest tests/test_seed_fallback_curation.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement seed script**

Create `scripts/seed_fallback_curation.py`:

```python
"""Fallback curation 시드.

books 테이블의 loan_count desc top 30 → fallback_curation 테이블.
Skip 유저 + 추천 서버 cold start fallback.

사용법:
  python3 scripts/seed_fallback_curation.py --dry-run
  python3 scripts/seed_fallback_curation.py
  python3 scripts/seed_fallback_curation.py --limit 50
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO, ".env"))


def rank_books_by_loan_count(books: list[dict]) -> list[dict]:
    """Sort by loan_count desc, drop rows with null loan_count."""
    valid = [b for b in books if b.get("loan_count") is not None]
    return sorted(valid, key=lambda b: b["loan_count"], reverse=True)


def build_fallback_rows(ranked: list[dict], limit: int = 30) -> list[dict]:
    """Build insert rows with sequential ranks starting at 1."""
    out = []
    for i, b in enumerate(ranked[:limit]):
        out.append({
            "rank": i + 1,
            "book_id": b["id"],
            "loan_count": b["loan_count"],
        })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args()

    from supabase import create_client
    sb = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )

    print(f"fetching books with loan_count (limit {args.limit * 3})...")
    res = (
        sb.table("books")
        .select("id, title, loan_count")
        .not_.is_("loan_count", "null")
        .order("loan_count", desc=True)
        .limit(args.limit * 3)
        .execute()
    )
    books = res.data or []
    print(f"  fetched {len(books)} candidate books")

    ranked = rank_books_by_loan_count(books)
    rows = build_fallback_rows(ranked, limit=args.limit)
    print(f"  ranked + truncated to {len(rows)} rows")

    if args.dry_run:
        print("(dry-run) sample rows:")
        for r in rows[:5]:
            print(f"  rank={r['rank']} book_id={r['book_id']} loan_count={r['loan_count']}")
        return

    sb.table("fallback_curation").delete().neq("rank", 0).execute()
    if rows:
        sb.table("fallback_curation").insert(rows).execute()
    print(f"✅ inserted {len(rows)} rows into fallback_curation")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Verify tests pass**

Run:
```bash
python3 -m pytest tests/test_seed_fallback_curation.py -v
```
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/20260408_fallback_curation.sql \
        scripts/seed_fallback_curation.py \
        tests/test_seed_fallback_curation.py
git commit -m "feat: fallback_curation 테이블 + 시드 스크립트"
```

---

## ⚠️ Eden 수동 적용 순서 (모든 task 완료 후)

1. SQL 마이그레이션 2개 적용 (Supabase SQL Editor):
   - `supabase/migrations/20260408_books_loan_count.sql`
   - `supabase/migrations/20260408_fallback_curation.sql`

2. Discovery collector 실행 (점진적):
   ```bash
   # Tier 1 dry-run으로 확인
   python3 scripts/data4library_discovery_collector.py --tier 1 --dry-run --pages 1
   # 실제 수집
   python3 scripts/data4library_discovery_collector.py --tier 1 --pages 2
   # Tier 2 (백카탈로그)
   python3 scripts/data4library_discovery_collector.py --tier 2 --tier2-seeds 50
   # Tier 3 (트렌드)
   python3 scripts/data4library_discovery_collector.py --tier 3
   ```

3. v3 인덱스 갱신 (별도 운영 작업):
   - 새로 들어온 책들에 대해 description/embedding 파이프라인 재실행 필요
   - 이 plan 범위 외. 기존 운영 도구에 의존

4. fallback curation 시드:
   ```bash
   python3 scripts/seed_fallback_curation.py --dry-run
   python3 scripts/seed_fallback_curation.py
   ```

5. recommendation-server push → Render 자동 배포 → `/similar/union` 라이브 확인

---

## Self-Review

**Spec coverage:**
- 정보나루 베스트셀러 수집 ✅ Task 3 (Tier 1)
- 정보나루 백카탈로그 발견 ✅ Task 4 (Tier 2 — recommandList)
- 시즌 트렌드 키워드 ✅ Task 5 (Tier 3 — monthlyKeywords + srchBooks)
- 어린이 필터 ✅ Task 1 (`is_adult_general`) + Task 3 적용
- 에디션 dedup ✅ Task 3 (`dedup_checker` 통합)
- `loan_count` 컬럼 ✅ Task 2
- POST /similar/union ✅ Task 6-7
- Fallback curation ✅ Task 8
- v3 인덱스 갱신: plan 범위 외 — Eden 수동 후속 작업

**Placeholder scan:**
- "TBD/TODO/구현 나중에" 없음 ✅
- 모든 코드 step에 실제 구현 코드 포함 ✅

**Type consistency:**
- `parse_book_docs` → dict with keys (`isbn13, title, author_raw, addition_symbol, kdc, ...`) ↔ `is_adult_general(book)` consumes `addition_symbol` ↔ `extract_first_author(author_raw)` ↔ `sanitize_for_upsert` ✅
- `select_seed_isbns_for_tier2` returns `list[str]` ↔ `fetch_tier2(seed_isbns)` ✅
- `parse_monthly_keywords` returns `list[tuple[str, float]]` ↔ `filter_single_token_keywords` ✅
- `similar_by_vector(query_vec, exclude_ids, limit)` ↔ `similar_union` 호출부 동일 ✅
- `SimilarUnionRequest` ↔ test ✅

**기존 자산 영향:**
- `scripts/data4library_collector.py` 건드리지 않음 ✅
- `scripts/lib/dedup_checker.py` 그대로 import ✅
- `recommendation-server/api/similar.py` 의 GET /similar/{book_id} 보존 ✅

**미해결/주의 사항:**
- 정보나루 일일 API 한도 미확인. REQUEST_DELAY=0.5s 보수적
- `recommandList` 응답에 `addition_symbol` 빈 값 가능 — `is_adult_general` 가 빈 값을 pass하도록 설계 (검증된 결정)
- 새 책의 v3 vector 부재 — Eden 수동 후속

---

Plan complete and saved to `docs/superpowers/plans/2026-04-08-onboarding-data-backend.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review

**2. Inline Execution** — execute in this session with checkpoints

Which approach?
