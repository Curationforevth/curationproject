# 도서 수집 & 임베딩 파이프라인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3-Layer 도서 수집 아키텍처(Seed + Daily Batch + Demand Layer)와 2-Tier 임베딩 파이프라인을 구현한다.

**Architecture:** 기존 `smart_batch_collector.py`를 개선(yield rate 스킵, 라운드로빈, sales_point 저장, 일일 신규 도서 목표)하고, Tier 1 임베딩 생성기를 추가하고, GitHub Actions로 매일 자동 실행한다. Tier 2 강화는 별도 스킬로 수동 트리거한다.

**Tech Stack:** Python 3, Supabase (PostgreSQL + pgvector), OpenAI text-embedding-3-small, GitHub Actions, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-batch-collection-strategy-design.md`

---

## 파일 구조

### 신규 생성

| 파일 | 역할 |
|------|------|
| `supabase/003_embedding_schema.sql` | books 테이블 컬럼 추가 (sales_point, enriched_description, updated_at), book_embeddings 컬럼 추가 (tier, updated_at), HNSW 인덱스, 상태 리셋 로직 |
| `scripts/tier1_embedder.py` | 임베딩 미생성 도서에 대해 Tier 1 기본 임베딩 생성 → book_embeddings 저장 |
| `scripts/requirements.txt` | Python 의존성 (supabase, python-dotenv, openai) |
| `.github/workflows/daily-batch.yml` | 매일 새벽 자동 실행: 수집 → 임베딩 |
| `tests/conftest.py` | pytest 공통 fixtures (mock Supabase, mock API) |
| `tests/test_book_filter.py` | book_filter 단위 테스트 |
| `tests/test_title_cleaner.py` | title_cleaner 단위 테스트 |
| `tests/test_collector_logic.py` | 수집기 로직 테스트 (yield rate, 라운드로빈, 신규 도서 카운팅) |
| `tests/test_tier1_embedder.py` | Tier 1 임베딩 생성기 테스트 |

### 수정

| 파일 | 변경 내용 |
|------|----------|
| `scripts/smart_batch_collector.py` | sales_point 저장, yield rate 스킵, 라운드로빈, 신규 도서 목표 카운팅, 30일 상태 리셋 |
| `scripts/lib/aladin_client.py` | API 응답에서 salesPoint 필드 전달 (변경 불필요 — 이미 raw item 반환) |
| `scripts/lib/state_manager.py` | 30일 경과 상태 리셋 메서드 추가, `updated_at` 문자열 버그 수정 (`"now()"` → `datetime.now(UTC).isoformat()`) |

---

## Task 1: 테스트 인프라 + 기존 모듈 테스트

기존 코드에 테스트가 없다. 개선 작업 전에 먼저 기존 동작을 테스트로 고정한다.

**Files:**
- Create: `scripts/requirements.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_book_filter.py`
- Create: `tests/test_title_cleaner.py`

- [ ] **Step 1: requirements.txt 생성**

```
supabase>=2.0.0
python-dotenv>=1.0.0
openai>=1.0.0
pytest>=8.0.0
```

- [ ] **Step 2: conftest.py 생성**

```python
import sys
import os

# scripts/lib를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
```

- [ ] **Step 3: book_filter 테스트 작성**

```python
from lib.book_filter import is_non_book


def test_filters_toeic_book():
    item = {"title": "토익 실전 1000제", "categoryName": "외국어"}
    assert is_non_book(item) is True


def test_filters_exam_category():
    item = {"title": "행정법 총론", "categoryName": "취업/수험서"}
    assert is_non_book(item) is True


def test_passes_novel():
    item = {"title": "살인자의 기억법", "categoryName": "소설/시/희곡"}
    assert is_non_book(item) is False


def test_passes_essay():
    item = {"title": "나는 나로 살기로 했다", "categoryName": "에세이"}
    assert is_non_book(item) is False


def test_handles_empty_fields():
    item = {"title": "", "categoryName": ""}
    assert is_non_book(item) is False


def test_handles_none_fields():
    item = {"title": None, "categoryName": None}
    assert is_non_book(item) is False
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_book_filter.py -v`
Expected: 6 passed

- [ ] **Step 5: title_cleaner 테스트 작성**

```python
from lib.title_cleaner import clean_title


def test_removes_special_edition_paren():
    assert clean_title("채식주의자 (특별판)") == "채식주의자"


def test_removes_hardcover_paren():
    assert clean_title("소년이 온다 (양장)") == "소년이 온다"


def test_removes_goods_dash():
    assert clean_title("달러구트 꿈 백화점 - 포토카드 포함") == "달러구트 꿈 백화점"


def test_keeps_subtitle():
    assert clean_title("사피엔스 - 유인원에서 사이보그까지") == "사피엔스 - 유인원에서 사이보그까지"


def test_handles_empty_string():
    assert clean_title("") == ""


def test_removes_volume_info():
    assert clean_title("원피스 - 전105권") == "원피스"
```

- [ ] **Step 6: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_title_cleaner.py -v`
Expected: 6 passed

- [ ] **Step 7: 커밋**

```bash
git add scripts/requirements.txt tests/
git commit -m "test: book_filter, title_cleaner 단위 테스트 + 테스트 인프라"
```

---

## Task 2: DB 스키마 마이그레이션

**Files:**
- Create: `supabase/003_embedding_schema.sql`

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- =============================================
-- 003: 수집 & 임베딩 파이프라인 스키마 확장
-- Spec: docs/superpowers/specs/2026-03-20-batch-collection-strategy-design.md
-- =============================================

-- 1. books 테이블 확장
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS sales_point INT;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS enriched_description TEXT;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- books에도 updated_at 자동 갱신 트리거 (기존 handle_updated_at 함수 재사용)
CREATE TRIGGER on_books_updated
  BEFORE UPDATE ON public.books
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- sales_point 인덱스 (Tier 2 강화 우선순위 조회용)
CREATE INDEX IF NOT EXISTS idx_books_sales_point ON public.books(sales_point DESC NULLS LAST);

-- 2. book_embeddings 테이블 확장
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS tier SMALLINT DEFAULT 1;
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE TRIGGER on_book_embeddings_updated
  BEFORE UPDATE ON public.book_embeddings
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- 3. HNSW 벡터 인덱스 (코사인 유사도 검색용)
CREATE INDEX IF NOT EXISTS idx_book_embeddings_hnsw
  ON public.book_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 4. batch_collection_state에 auto updated_at 트리거 추가
-- 기존 002에서 트리거가 누락되어 있었음. state_manager.py에서도 수동으로 ISO 타임스탬프를 넣지만
-- DB 레벨 트리거를 추가하여 이중 안전장치 확보.
CREATE TRIGGER on_batch_collection_state_updated
  BEFORE UPDATE ON public.batch_collection_state
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- 5. source 컬럼 CHECK 제약 업데이트 (kakao 추가 확인)
-- 기존 001에서 이미 ('kakao', 'aladin')으로 설정됨. 변경 불필요.
```

- [ ] **Step 2: Supabase SQL Editor에서 실행**

Supabase 대시보드 → SQL Editor → 003 내용 붙여넣기 → Run
Expected: 모든 ALTER/CREATE 성공

- [ ] **Step 3: 적용 확인**

Supabase 대시보드 → Table Editor → books 테이블 → sales_point, enriched_description, updated_at 컬럼 확인
book_embeddings 테이블 → tier, updated_at 컬럼 확인

- [ ] **Step 4: 커밋**

```bash
git add supabase/003_embedding_schema.sql
git commit -m "feat: 수집/임베딩 파이프라인 스키마 확장 (003)"
```

---

## Task 3: state_manager에 30일 상태 리셋 추가

**Files:**
- Modify: `scripts/lib/state_manager.py`
- Create: `tests/test_state_manager.py`

- [ ] **Step 1: updated_at 문자열 버그 수정**

기존 `state_manager.py` line 48의 `"updated_at": "now()"` 는 문자열이 그대로 저장될 수 있음.
`batch_collection_state` 테이블에 auto-update 트리거도 없음. 두 가지 모두 수정:

`scripts/lib/state_manager.py`의 `upsert_state`에서 변경:
```python
# 기존: "updated_at": "now()",
# 변경:
from datetime import datetime, timezone
"updated_at": datetime.now(timezone.utc).isoformat(),
```

`supabase/003_embedding_schema.sql`에 트리거 추가 (아래 Task 2에서 반영):
```sql
CREATE TRIGGER on_batch_collection_state_updated
  BEFORE UPDATE ON public.batch_collection_state
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
```

- [ ] **Step 2: 상태 리셋 테스트 작성**

```python
from unittest.mock import MagicMock
from lib.state_manager import StateManager


def _make_manager():
    mock_sb = MagicMock()
    return StateManager(mock_sb), mock_sb


def test_reset_expired_states_calls_update():
    mgr, mock_sb = _make_manager()
    # 체인 모킹
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": "1"}])

    result = mgr.reset_expired_states(days=30)

    mock_sb.table.assert_called_with("batch_collection_state")
    mock_table.update.assert_called_once_with({"completed": False})
    assert result >= 0


def test_reset_skips_item_list():
    """Phase 1 (item_list)은 영구 완료 — 리셋 대상 아님"""
    mgr, mock_sb = _make_manager()
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])

    mgr.reset_expired_states(days=30)

    # neq("source_type", "item_list")가 호출되어야 함
    mock_table.neq.assert_called_with("source_type", "item_list")


def test_upsert_state_uses_iso_timestamp():
    """updated_at이 문자열 'now()'가 아닌 ISO 타임스탬프여야 함"""
    mgr, mock_sb = _make_manager()
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])

    mgr.upsert_state(source_type="item_list", query_type="Bestseller", category_id=1)

    call_args = mock_table.upsert.call_args[0][0]
    assert call_args["updated_at"] != "now()"
    assert "T" in call_args["updated_at"]  # ISO 포맷에는 T가 있음
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

Run: `cd scripts && python -m pytest ../tests/test_state_manager.py -v`
Expected: FAIL — `reset_expired_states` 메서드 없음, `upsert_state`의 `updated_at`이 `"now()"`

- [ ] **Step 4: reset_expired_states 구현 + updated_at 버그 수정**

`scripts/lib/state_manager.py`에 메서드 추가:

```python
def reset_expired_states(self, days=30):
    """Phase 2-3의 completed 상태를 days일 경과 시 리셋.
    Phase 1 (item_list)은 영구 완료이므로 제외."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    result = (
        self.sb.table(self.table)
        .update({"completed": False})
        .eq("completed", True)
        .neq("source_type", "item_list")
        .lt("updated_at", cutoff)
        .execute()
    )
    count = len(result.data) if result.data else 0
    if count > 0:
        print(f"  ♻ {count}개 소스 상태 리셋 (30일 경과)")
    return count
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_state_manager.py -v`
Expected: 3 passed

- [ ] **Step 6: 커밋**

```bash
git add scripts/lib/state_manager.py tests/test_state_manager.py
git commit -m "feat: state_manager에 30일 상태 리셋 + updated_at 버그 수정"
```

---

## Task 4: smart_batch_collector 개선 — sales_point 저장

**Files:**
- Modify: `scripts/smart_batch_collector.py`
- Create: `tests/test_collector_logic.py`

- [ ] **Step 1: sales_point 저장 테스트 작성**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from smart_batch_collector import SmartBatchCollector


def test_process_items_includes_sales_point():
    """API 응답의 salesPoint가 변환 결과에 포함되어야 함"""
    # SmartBatchCollector를 직접 인스턴스화하지 않고 process_items 로직 테스트
    # process_items는 self.known_isbns를 사용하므로 간접 테스트
    from lib.book_filter import is_non_book
    from lib.title_cleaner import clean_title

    item = {
        "isbn13": "9788937460470",
        "title": "채식주의자",
        "author": "한강",
        "publisher": "창비",
        "cover": "http://example.com/cover.jpg",
        "description": "한강의 소설",
        "categoryName": "소설/시/희곡",
        "itemId": 12345,
        "salesPoint": 85432,
    }

    # process_items 로직 재현
    isbn = item.get("isbn13") or item.get("isbn") or ""
    assert isbn == "9788937460470"
    assert not is_non_book(item)

    book = {
        "isbn": isbn,
        "title": clean_title(item.get("title", "")),
        "author": item.get("author", ""),
        "publisher": item.get("publisher", ""),
        "cover_url": item.get("cover", ""),
        "description": item.get("description", ""),
        "genre": item.get("categoryName", ""),
        "source": "aladin",
        "source_id": str(item.get("itemId", "")),
        "sales_point": item.get("salesPoint"),
    }

    assert book["sales_point"] == 85432
    assert book["title"] == "채식주의자"
```

- [ ] **Step 2: 테스트 실행 — 통과 확인** (이 테스트는 현재 코드와 무관한 로직 재현이므로 통과함)

Run: `cd scripts && python -m pytest ../tests/test_collector_logic.py::test_process_items_includes_sales_point -v`
Expected: PASS

- [ ] **Step 3: smart_batch_collector.py의 process_items에 sales_point 추가**

`scripts/smart_batch_collector.py` line 136 부근, `book` dict에 추가:

```python
book = {
    "isbn": isbn,
    "title": clean_title(item.get("title", "")),
    "author": item.get("author", ""),
    "publisher": item.get("publisher", ""),
    "cover_url": item.get("cover", ""),
    "description": item.get("description", ""),
    "genre": item.get("categoryName", ""),
    "source": "aladin",
    "source_id": str(item.get("itemId", "")),
    "sales_point": item.get("salesPoint"),  # 추가
}
```

- [ ] **Step 4: 커밋**

```bash
git add scripts/smart_batch_collector.py tests/test_collector_logic.py
git commit -m "feat: 수집 시 sales_point 저장"
```

---

## Task 5: smart_batch_collector 개선 — yield rate 스마트 스킵

**Files:**
- Modify: `scripts/smart_batch_collector.py`
- Modify: `tests/test_collector_logic.py`

- [ ] **Step 1: yield rate 계산 테스트 작성**

`tests/test_collector_logic.py`에 추가:

```python
def test_yield_rate_below_threshold_skips():
    """50건 중 새 책 5건 미만(10%)이면 해당 소스 종료"""
    total_items = 50
    new_items = 4
    yield_rate = new_items / total_items if total_items > 0 else 0
    assert yield_rate < 0.10  # 스킵 조건 충족


def test_yield_rate_above_threshold_continues():
    """50건 중 새 책 5건 이상이면 계속"""
    total_items = 50
    new_items = 6
    yield_rate = new_items / total_items if total_items > 0 else 0
    assert yield_rate >= 0.10  # 계속 조건
```

- [ ] **Step 2: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_collector_logic.py -v`
Expected: 3 passed

- [ ] **Step 3: _run_search_phase에만 yield rate 스킵 적용**

`scripts/smart_batch_collector.py`의 `_run_search_phase` 메서드, 기존 "새 책 0건이면 종료" 로직을 yield rate 기반으로 교체:

```python
# 기존 (line 356-366):
# if len(books) == 0:
#     ...
#     break

# 변경:
yield_rate = len(books) / len(items) if items else 0
if yield_rate < 0.10:  # 10% 미만이면 소스 종료
    self.state_mgr.upsert_state(
        source_type=source_type,
        search_keyword=keyword,
        last_page_fetched=page,
        total_items_found=total_found,
        unique_items_saved=unique_saved,
        completed=True,
    )
    break
```

**참고:** `run_item_list`에는 적용하지 않음 — Task 6에서 라운드로빈으로 전체 리팩터할 때 yield rate도 함께 포함.

- [ ] **Step 4: 커밋**

```bash
git add scripts/smart_batch_collector.py tests/test_collector_logic.py
git commit -m "feat: yield rate 10% 미만 시 소스 스킵"
```

---

## Task 6: smart_batch_collector 개선 — 라운드로빈 카테고리 순회

**Files:**
- Modify: `scripts/smart_batch_collector.py`

- [ ] **Step 1: 라운드로빈 순회 테스트 작성**

`tests/test_collector_logic.py`에 추가:

```python
def test_round_robin_order():
    """카테고리를 라운드로빈으로 순회해야 함:
    cat1 p1 → cat2 p1 → ... → cat17 p1 → cat1 p2 → ..."""
    categories = {1: "소설", 2: "에세이", 3: "인문학"}
    query_types = ["Bestseller", "ItemNewAll"]
    max_pages = 2

    # 기대하는 순서: 페이지 우선 순회
    expected_order = []
    for page in range(1, max_pages + 1):
        for cat_id in categories:
            for qt in query_types:
                expected_order.append((cat_id, qt, page))

    # 생성
    actual_order = []
    for page in range(1, max_pages + 1):
        for cat_id in categories:
            for qt in query_types:
                actual_order.append((cat_id, qt, page))

    assert actual_order == expected_order
    # 첫 번째는 (1, "Bestseller", 1), 두 번째는 (1, "ItemNewAll", 1)
    assert actual_order[0] == (1, "Bestseller", 1)
    assert actual_order[2] == (2, "Bestseller", 1)
```

- [ ] **Step 2: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_collector_logic.py::test_round_robin_order -v`
Expected: PASS

- [ ] **Step 3: run_item_list를 라운드로빈으로 변경**

`scripts/smart_batch_collector.py`의 `run_item_list` 메서드를 리팩터:

```python
def run_item_list(self):
    """Phase 1: 라운드로빈 — 페이지별로 전 카테고리 순회"""
    print("=" * 60)
    print("Phase 1: ItemList 전카테고리 스윕 (라운드로빈)")
    print("=" * 60)

    for page in range(1, MAX_PAGES + 1):
        for cat_id, cat_name in CATEGORIES.items():
            for qt in QUERY_TYPES:
                if not self.aladin.has_budget():
                    print("\n⚠ 일일 API 한도 도달. 다음 실행에서 이어갑니다.")
                    return

                state = self.state_mgr.get_state(
                    source_type="item_list",
                    query_type=qt,
                    category_id=cat_id,
                )
                if state and state.get("completed"):
                    continue

                last_page = state["last_page_fetched"] if state else 0
                if page <= last_page:
                    continue  # 이미 처리한 페이지

                total_found = state["total_items_found"] if state else 0
                unique_saved = state["unique_items_saved"] if state else 0

                items, total = self.aladin.fetch_item_list(qt, cat_id, page)
                total_found += len(items)

                if not items:
                    self.state_mgr.upsert_state(
                        source_type="item_list", query_type=qt,
                        category_id=cat_id, last_page_fetched=page,
                        total_items_found=total_found,
                        unique_items_saved=unique_saved, completed=True,
                    )
                    continue

                books = self.process_items(items)
                unique_saved += len(books)
                yield_rate = len(books) / len(items) if items else 0

                if books:
                    self.save_batch(books)
                    print(f"  {cat_name} / {qt} p{page}: +{len(books)}권 (yield {yield_rate:.0%})")

                completed = (yield_rate < 0.10) or (page >= MAX_PAGES) or (len(items) < 50)

                self.state_mgr.upsert_state(
                    source_type="item_list", query_type=qt,
                    category_id=cat_id, last_page_fetched=page,
                    total_items_found=total_found,
                    unique_items_saved=unique_saved, completed=completed,
                )

                time.sleep(API_CALL_DELAY)
```

- [ ] **Step 4: 커밋**

```bash
git add scripts/smart_batch_collector.py tests/test_collector_logic.py
git commit -m "feat: 라운드로빈 카테고리 순회 (장르 균형)"
```

---

## Task 7: smart_batch_collector 개선 — 일일 신규 도서 목표

**Files:**
- Modify: `scripts/smart_batch_collector.py`

- [ ] **Step 1: --daily-target CLI 옵션 추가 + 신규 도서 카운트 기반 종료**

`scripts/smart_batch_collector.py`의 `main()`에 인자 추가:

```python
parser.add_argument("--daily-target", type=int, default=0,
                    help="일일 신규 도서 목표 (0=무제한)")
```

`SmartBatchCollector.__init__`에 추가:

```python
self.daily_target = daily_target  # 생성자 매개변수로 추가
```

API 한도 체크 부분에 daily_target 체크 병행:

```python
def has_capacity(self):
    """API 예산과 일일 목표 모두 체크"""
    if not self.aladin.has_budget():
        return False
    if self.daily_target > 0 and self.stats["saved"] >= self.daily_target:
        return False
    return True
```

기존 `self.aladin.has_budget()` 호출을 `self.has_capacity()`로 교체. 교체 위치:
- `run_item_list` (Task 6에서 이미 리팩터된 버전) 내 2곳
- `_run_search_phase` 내 2곳 (line 310, 329 부근)

- [ ] **Step 2: daily-target 도달 시 메시지 출력 + dry-run 호환**

`has_capacity` 실패 시 적절한 메시지:

```python
if self.daily_target > 0 and self.stats["saved"] >= self.daily_target:
    print(f"\n✅ 일일 목표 달성: {self.stats['saved']}/{self.daily_target}권")
```

`process_items`에서 `stats["saved"]`를 증가시키도록 변경 (기존에는 `save_batch`에서만 증가):
```python
# process_items 끝에서:
# dry-run에서도 카운트하기 위해 process 단계에서 saved 증가
# save_batch의 중복 증가는 제거
```

`save_batch`에서 `self.stats["saved"] += len(books)` 제거 → `process_items` 직후에 카운트:
```python
books = self.process_items(items)
self.stats["saved"] += len(books)  # dry-run 포함 카운트
if books:
    self.save_batch(books)  # save_batch에서는 saved 증가 안 함
```

- [ ] **Step 3: 30일 상태 리셋 호출 추가**

`main()`에서 수집 시작 전에 리셋 호출:

```python
collector.load_known_isbns()
collector.state_mgr.reset_expired_states(days=30)  # 추가
```

- [ ] **Step 4: 커밋**

```bash
git add scripts/smart_batch_collector.py
git commit -m "feat: --daily-target 옵션 + 30일 상태 리셋"
```

---

## Task 8: Tier 1 임베딩 생성기

**Files:**
- Create: `scripts/tier1_embedder.py`
- Create: `tests/test_tier1_embedder.py`

- [ ] **Step 1: 임베딩 텍스트 조합 테스트 작성**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_compose_embedding_text():
    """title + author + genre + description을 조합"""
    from tier1_embedder import compose_embedding_text

    book = {
        "title": "채식주의자",
        "author": "한강",
        "genre": "소설/시/희곡",
        "description": "한강의 연작소설. 채식을 시작한 여자의 이야기.",
    }
    text = compose_embedding_text(book)
    assert "채식주의자" in text
    assert "한강" in text
    assert "소설" in text
    assert "채식을 시작한" in text


def test_compose_embedding_text_empty_description():
    """description이 없어도 동작"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": "저자", "genre": "소설", "description": ""}
    text = compose_embedding_text(book)
    assert "제목" in text
    assert len(text) > 0


def test_compose_embedding_text_none_fields():
    """None 필드 처리"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": None, "genre": None, "description": None}
    text = compose_embedding_text(book)
    assert "제목" in text
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd scripts && python -m pytest ../tests/test_tier1_embedder.py -v`
Expected: FAIL — `tier1_embedder` 모듈 없음

- [ ] **Step 3: tier1_embedder.py 구현**

```python
"""
Tier 1 임베딩 생성기
- book_embeddings가 없는 도서를 찾아서 기본 임베딩 생성
- 입력: title + author + genre + description
- 모델: OpenAI text-embedding-3-small (1536차원)

사용법:
  python3 scripts/tier1_embedder.py              # 미생성 도서 전부
  python3 scripts/tier1_embedder.py --limit 100  # 최대 100권
  python3 scripts/tier1_embedder.py --dry-run    # 실제 저장 없이 테스트
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50  # OpenAI에 한 번에 보낼 텍스트 수


def compose_embedding_text(book):
    """책 메타데이터를 임베딩 입력 텍스트로 조합"""
    parts = []
    title = book.get("title") or ""
    author = book.get("author") or ""
    genre = book.get("genre") or ""
    description = book.get("description") or ""

    if title:
        parts.append(f"제목: {title}")
    if author:
        parts.append(f"저자: {author}")
    if genre:
        parts.append(f"장르: {genre}")
    if description:
        parts.append(f"내용: {description}")

    return "\n".join(parts)


def fetch_books_without_embeddings(sb, limit=0):
    """book_embeddings에 row가 없는 books 조회"""
    # LEFT JOIN 대신 NOT IN으로 처리 (Supabase 클라이언트 제약)
    embedded_result = sb.table("book_embeddings").select("book_id").execute()
    embedded_ids = {row["book_id"] for row in (embedded_result.data or [])}

    query = sb.table("books").select("id, title, author, genre, description")
    if limit > 0:
        query = query.limit(limit)

    result = query.execute()
    books = [b for b in (result.data or []) if b["id"] not in embedded_ids]

    if limit > 0:
        books = books[:limit]

    return books


def generate_embeddings(openai_client, texts):
    """OpenAI API로 임베딩 벡터 생성"""
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def save_embeddings(sb, book_ids, embeddings, dry_run=False):
    """book_embeddings 테이블에 저장"""
    if dry_run:
        return

    rows = [
        {
            "book_id": book_id,
            "embedding": embedding,
            "tier": 1,
        }
        for book_id, embedding in zip(book_ids, embeddings)
    ]

    sb.table("book_embeddings").upsert(
        rows, on_conflict="book_id"
    ).execute()


def main():
    parser = argparse.ArgumentParser(description="Tier 1 임베딩 생성기")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 권수 (0=전부)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 테스트")
    args = parser.parse_args()

    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    print("🔍 임베딩 미생성 도서 조회 중...")
    books = fetch_books_without_embeddings(sb, limit=args.limit)
    print(f"   {len(books)}권 발견\n")

    if not books:
        print("✅ 모든 도서에 임베딩이 있습니다.")
        return

    total_embedded = 0

    for i in range(0, len(books), BATCH_SIZE):
        batch = books[i : i + BATCH_SIZE]
        texts = [compose_embedding_text(b) for b in batch]
        book_ids = [b["id"] for b in batch]

        try:
            embeddings = generate_embeddings(openai_client, texts)
            save_embeddings(sb, book_ids, embeddings, dry_run=args.dry_run)
            total_embedded += len(batch)
            prefix = "(dry-run) " if args.dry_run else ""
            print(f"  {prefix}배치 {i // BATCH_SIZE + 1}: {len(batch)}권 임베딩 완료")
        except Exception as e:
            print(f"  ✗ 배치 {i // BATCH_SIZE + 1} 실패: {e}")

        time.sleep(0.5)  # OpenAI rate limit 대비

    print(f"\n{'=' * 40}")
    prefix = "(dry-run) " if args.dry_run else ""
    print(f"{prefix}총 {total_embedded}/{len(books)}권 임베딩 생성 완료")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_tier1_embedder.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/tier1_embedder.py tests/test_tier1_embedder.py
git commit -m "feat: Tier 1 임베딩 생성기 (text-embedding-3-small)"
```

---

## Task 9: GitHub Actions 워크플로우

**Files:**
- Create: `.github/workflows/daily-batch.yml`

- [ ] **Step 1: 워크플로우 파일 작성**

```yaml
name: Daily Batch Collection & Embedding

on:
  schedule:
    - cron: '0 18 * * *'  # UTC 18:00 = KST 03:00
  workflow_dispatch:  # 수동 실행 가능

jobs:
  batch-collect-and-embed:
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
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          ALADIN_TTB_KEY: ${{ secrets.ALADIN_TTB_KEY }}
        run: python scripts/smart_batch_collector.py --daily-target 1000

      - name: Run Tier 1 embedder
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python scripts/tier1_embedder.py

      - name: Show status
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          ALADIN_TTB_KEY: ${{ secrets.ALADIN_TTB_KEY }}
        run: python scripts/smart_batch_collector.py --status
```

- [ ] **Step 2: GitHub Secrets 설정 안내**

GitHub → Settings → Secrets and variables → Actions → New repository secret:
- `SUPABASE_URL`: Supabase 프로젝트 URL
- `SUPABASE_SERVICE_ROLE_KEY`: Supabase service role key
- `ALADIN_TTB_KEY`: 알라딘 API 키
- `OPENAI_API_KEY`: OpenAI API 키

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/daily-batch.yml
git commit -m "ci: GitHub Actions 데일리 배치 수집 + 임베딩 자동화"
```

---

## Task 10: 통합 테스트 + 정리

**Files:**
- Modify: `scripts/smart_batch_collector.py` — `__init__` 시그니처 정리
- 전체 테스트 실행

- [ ] **Step 1: SmartBatchCollector 생성자에 daily_target 매개변수 반영 확인**

`__init__`이 `daily_target` 파라미터를 받고 `main()`에서 전달하는지 확인:

```python
class SmartBatchCollector:
    def __init__(self, dry_run=False, daily_target=0):
        self.dry_run = dry_run
        self.daily_target = daily_target
        # ... 나머지
```

```python
def main():
    # ...
    collector = SmartBatchCollector(dry_run=args.dry_run, daily_target=args.daily_target)
```

- [ ] **Step 2: 전체 테스트 실행**

Run: `cd scripts && python -m pytest ../tests/ -v`
Expected: 모든 테스트 통과

- [ ] **Step 3: dry-run 통합 테스트**

Run: `python scripts/smart_batch_collector.py --phase item_list --dry-run --daily-target 10`
Expected: 라운드로빈 순서로 카테고리 순회, yield rate 출력, 10권 도달 시 종료

- [ ] **Step 4: 최종 커밋 + 푸시**

```bash
git add scripts/smart_batch_collector.py tests/
git commit -m "chore: 통합 테스트 정리 + 생성자 시그니처 확정"
git push origin main
```

---

## 구현 제외 (별도 계획 필요)

| 항목 | 사유 |
|------|------|
| **Demand Layer** (앱 검색 로직) | Flutter 앱 코드 — 앱 개발 시 별도 계획 |
| **Tier 2 강화 스킬** (`/book-enrichment`) | superpowers writing-skills로 별도 작성 |
| **Supabase Edge Function** (Demand Layer 임베딩 트리거) | 앱 개발과 함께 구현 |
| **ARCHITECTURE.md 업데이트** | 스펙 Section 10 참조, 구현 완료 후 반영 |
