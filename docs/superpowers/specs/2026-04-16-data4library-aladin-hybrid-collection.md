# 정보나루 + 알라딘 혼합 수집 전략

> 2026-04-16 | Eden
> 관련 spec: `2026-03-20-batch-collection-strategy-design.md`, `2026-03-26-onboarding-design.md`, `2026-04-07-curation-system-design.md`

## 1. 배경

### 기존 문제 1: loan_count 소스 불일치

`books.loan_count` 가 두 가지 다른 의미로 섞여 저장되어 있음.

- `loanItemSrch` 에서 가져온 값: "특정 기간 내 대출수" (기간 파라미터에 따라 변동)
- `recommandList` 결과: `loan_count = null` (API가 반환 안 함)
- `books.sales_point` 컬럼이 일부 코드에서 `loan_count` 값으로 덮어써지는 버그

→ fallback_curation 랭킹이 공정하지 않음.

### 기존 문제 2: 수집 속도 정체

현재 daily-pipeline 이 `loanItemSrch × 10 KDC × 1 page × 30일` 만 실행 → 매일 top 500을 반복 수집. 중복 제외 신규는 50~100권/일 수준으로 감소.

→ 목표 20,000권까지 속도 부족.

### 기존 문제 3: 신간 불리

정보나루 `loanItemSrch.loan_count` 는 기간 누적이라 스테디셀러 유리. 완전 초기 신간(출간 수개월) 은 수치 낮음.

알라딘 `sales_point` 는 판매 기반이라 출간 즉시 높음. 두 소스가 보완적이지만 현재 파이프라인은 두 소스 값을 별개 컬럼으로만 저장, 랭킹에 혼합 사용 안 함.

### 실측 결과 (2026-04-16)

정보나루 loanItemSrch top 20 vs 알라딘 Bestseller top 10 교집합: **0권**.

- 정보나루: 문학 위주 (한강, 김호연, 양귀자, 김영하, 김애란)
- 알라딘: 인문/자기계발/에세이 (유시민, 자청, 신영준, 그라시안)

두 소스를 섞으면 책 풀이 극적으로 확장.

---

## 2. 결정 사항

### 2.1 loan_count 소스 통일

`books.loan_count` 는 **usageAnalysisList.book.loanCnt** (누적 전체 대출수) 로 통일.

- 모든 신규 수집 시 usageAnalysisList 호출 후처리 단계 필수
- 기존 책은 1회 backfill 로 값 통일
- `loanItemSrch.loan_count` 는 ISBN 발견용으로만 사용, DB 저장 안 함

### 2.2 loan_count_12mo 신설

최근 12개월 대출수를 별도 컬럼으로 저장. `sum(usageAnalysisList.loanHistory.loanCnt)`.

- "최근 트렌드" 시그널
- 같은 API 호출에서 함께 파싱 (추가 API 호출 없음)

### 2.3 sales_point 용도 분리

- `sales_point` = 알라딘 SalesPoint 전용 (판매 기반)
- `loan_count` = 정보나루 usageAnalysisList 전용 (대출 기반)
- 혼합은 랭킹 단계에서만. 컬럼은 분리 유지.

### 2.4 Strategy C (Complementary fill) 로 혼합

두 소스를 **동일 섹션 안에서 보완적으로 섞음**. 별도 섹션 분리 안 함.

### 2.5 동일 작품 dedup 규칙 변경

기존: 같은 title+author 발견 시 "첫 수집 ISBN" 이 대표, 이후 SKIP.

신규: 같은 title+author 발견 시 **새 ISBN 의 loan_count > 기존 loan_count** 면 기존 row 의 `loan_count`/`loan_count_12mo` 만 UPDATE. ISBN/title/cover/author/description 등은 **건드리지 않음**.

이유: 재임베딩/재추출/YES24 재스크랩 회피. UX(서재 표지) 안정성 유지. FK 무영향.

---

## 3. 스키마 변경

```sql
ALTER TABLE books
  ADD COLUMN loan_count_12mo INT,
  ADD COLUMN loan_count_source TEXT,        -- 'usageAnalysisList' / 'loanItemSrch' / null
  ADD COLUMN loan_count_updated_at TIMESTAMPTZ;
```

`sales_point` 는 이미 존재. 변경 없음.

**fallback_curation** 테이블 변경 없음. refresh 함수만 재작성.

---

## 4. 수집 파이프라인

```
[Tier 1 발견] loanItemSrch × KDC × 기간
   → ISBN 만 수집 (loan_count 안 씀)

[Tier 2 확장] recommandList seed → related ISBN
   → ISBN 만 수집

[Tier 3 트렌드] monthlyKeywords → srchBooks
   → ISBN 만 수집

        ↓ 모든 신규 ISBN

[usageAnalysisList 후처리 — 신규 필수 단계]
   → book.loanCnt                → books.loan_count
   → sum(loanHistory.loanCnt)    → books.loan_count_12mo
   → library_keywords             → books.library_keywords (기존)
   → coLoanBooks.isbn13           → books.related_isbns (기존)
   → loan_count_source            = 'usageAnalysisList'
   → loan_count_updated_at        = NOW()

[dedup_checker]
   같은 title+author 가 이미 있으면:
      if new.loan_count > existing.loan_count:
         UPDATE books SET loan_count=?, loan_count_12mo=?,
                          loan_count_updated_at=NOW()
         WHERE id = existing.id
      else:
         SKIP (새 ISBN row 생성 안 함)

[알라딘 smart_batch_collector] (병행)
   → sales_point 채움 (기존 로직 유지)
```

---

## 5. fallback_curation refresh (Strategy C)

```sql
CREATE OR REPLACE FUNCTION refresh_fallback_curation() RETURNS void AS $$
BEGIN
  DELETE FROM fallback_curation;
  
  INSERT INTO fallback_curation (rank, book_id, loan_count, added_at)
    WITH
    -- 1단계: 정보나루 loan_count_12mo 상위 20 (제목 dedup)
    d4l AS (
      SELECT DISTINCT ON (title) id, title, loan_count_12mo, loan_count
      FROM books
      WHERE loan_count_12mo IS NOT NULL
      ORDER BY title, loan_count_12mo DESC NULLS LAST
    ),
    d4l_top AS (
      SELECT id, loan_count_12mo, loan_count, 1 AS priority
      FROM d4l ORDER BY loan_count_12mo DESC LIMIT 20
    ),
    -- 2단계: 알라딘 sales_point top 중 d4l_top 에 없는 책 10권
    aladin_new AS (
      SELECT b.id, b.sales_point AS loan_count_12mo, b.loan_count, 2 AS priority
      FROM books b
      LEFT JOIN (
        SELECT d.id, b2.title FROM d4l_top d JOIN books b2 ON b2.id = d.id
      ) dt ON dt.title = b.title
      WHERE b.sales_point IS NOT NULL
        AND b.sales_point > 0
        AND dt.id IS NULL
      ORDER BY b.sales_point DESC LIMIT 10
    ),
    combined AS (
      SELECT id, loan_count_12mo AS sort_val, loan_count, priority FROM d4l_top
      UNION ALL
      SELECT id, loan_count_12mo AS sort_val, loan_count, priority FROM aladin_new
    )
    SELECT
      ROW_NUMBER() OVER (ORDER BY priority, sort_val DESC NULLS LAST),
      id,
      loan_count,   -- 실제 books.loan_count (누적). 알라딘 전용 책은 NULL 가능.
      NOW()
    FROM combined
    LIMIT 30;
END;
$$ LANGUAGE plpgsql;
```

결과: d4l 20 + 알라딘 10 = 총 30권. 순서는 d4l 먼저 (priority 1), 알라딘 뒤 (priority 2).

**fallback_curation.loan_count 컬럼 의미**: books.loan_count (누적 대출) 을 그대로 저장. display/debug 용. 알라딘 경로로 들어온 책은 NULL 가능 (정보나루 데이터 없음).

---

## 6. 큐레이션 내부 랭킹 (genre_combo / keyword / cluster)

> ⚠️ **상태: Phase 2 이월 (미구현)**. 현재 `refresh_curation_cache_all()`
> (migration `20260415_phase1b_12_functions_curation.sql`) 은 아직
> `ORDER BY loan_count DESC NULLS LAST` 를 사용한다. 아래 혼합 점수는
> 출시 후 30일 CTR 데이터를 보고 적용한다. fallback_curation (§5) 만 Strategy C
> 혼합이 적용된 상태.

기존: `ORDER BY loan_count DESC NULLS LAST`

신규: 혼합 점수

```sql
ORDER BY (
  COALESCE(loan_count_12mo, 0) * 2    -- W_RECENT
  + COALESCE(loan_count, 0) * 1       -- W_CUMULATIVE
  + COALESCE(sales_point, 0) * 0.5    -- W_SALES
) DESC
```

**저자 큐레이션 (by_author)** 는 변경 없음 — 작가별 대표작 위주라 `loan_count` 누적 유지.

### 가중치 (Layer 2 — 실측 후 조정)

| 변수 | 초기 추정 | 조정 기준 |
|------|---------|-----------|
| W_RECENT | 2.0 | 최근 12개월 대출. 신간/트렌드 반영 |
| W_CUMULATIVE | 1.0 | 누적 대출. 스테디셀러 |
| W_SALES | 0.5 | 알라딘 판매. 완전 초기 신간 보조 |

출시 후 30일 CTR 보고 조정.

---

## 7. 코드 변경

### 7.1 `lib/data4library_api.py`

```python
def parse_usage_analysis(response: dict) -> dict:
    """Parse usageAnalysisList response.
    
    Returns:
        {
          'loan_count': int,          # book.loanCnt
          'loan_count_12mo': int,     # sum(loanHistory.loanCnt)
          'library_keywords': [...],
          'co_loan_isbns': [...],
        }
    """
    resp = response.get('response', {})
    book = resp.get('book', {}) or {}
    lh = resp.get('loanHistory', []) or []
    return {
        'loan_count': int(book.get('loanCnt') or 0),
        'loan_count_12mo': sum(
            int(h.get('loan', {}).get('loanCnt') or 0) for h in lh
        ),
        # 기존 필드도 포함
        ...
    }
```

### 7.2 `lib/dedup_checker.py`

기존 `_normalize_for_dedup()` / `_normalize_author()` 는 그대로 사용.

**자료구조 변경**: `title_index` 값이 ISBN 문자열 리스트 → **dict 리스트** 로 확장.

```python
# 기존: self.title_index[key] = [isbn, isbn, ...]
# 신규: self.title_index[key] = [
#   {'isbn': ..., 'book_id': ..., 'loan_count': ...},
#   ...
# ]

def load_title_index(self):
    """DB 에서 (title, author) → [{isbn, book_id, loan_count}] 인덱스 구축."""
    offset = 0
    while True:
        result = self.sb.table("books").select(
            "id, isbn, title, author, loan_count"
        ).range(offset, offset + 1000 - 1).execute()
        if not result.data:
            break
        for row in result.data:
            key = (
                _normalize_for_dedup(clean_title(row.get("title") or "")),
                _normalize_author(row.get("author") or ""),
            )
            self.title_index[key].append({
                "isbn": row.get("isbn") or "",
                "book_id": row["id"],
                "loan_count": row.get("loan_count") or 0,
            })
        if len(result.data) < 1000:
            break
        offset += 1000
```

**신규 `check()` 메서드** (기존 `is_title_duplicate()` 은 호환용으로 유지):

```python
from enum import Enum
from typing import Optional

class DedupAction(Enum):
    NEW = "new"                      # 신규 ISBN, INSERT
    SKIP = "skip"                    # 동일 작품 + 낮은 loan_count, 스킵
    UPDATE_LOAN_COUNT = "update"     # 동일 작품 + 높은 loan_count, 기존 row UPDATE

def check(self, title, author, isbn, loan_count) -> tuple[DedupAction, Optional[str]]:
    """Returns (action, existing_book_id or None)."""
    key = (
        _normalize_for_dedup(clean_title(title or "")),
        _normalize_author(author or ""),
    )
    existing = self.title_index.get(key, [])
    if not existing:
        return (DedupAction.NEW, None)
    # 같은 ISBN 이면 upsert 대상 (loan_count 갱신 포함)
    same_isbn = next((e for e in existing if e['isbn'] == isbn), None)
    if same_isbn:
        return (DedupAction.NEW, None)  # upsert_books_rich_merge 가 알아서 처리
    # 다른 ISBN 이면 loan_count 비교
    best = max(existing, key=lambda e: e.get('loan_count') or 0)
    if loan_count > (best.get('loan_count') or 0):
        return (DedupAction.UPDATE_LOAN_COUNT, best['book_id'])
    return (DedupAction.SKIP, None)
```

### 7.3 `lib/books_upsert.py`

새 함수 추가:

```python
from datetime import datetime, timezone

def update_loan_count_by_book_id(sb, book_id, loan_count, loan_count_12mo):
    """Update only loan_count/loan_count_12mo for an existing book."""
    sb.table('books').update({
        'loan_count': loan_count,
        'loan_count_12mo': loan_count_12mo,
        'loan_count_source': 'usageAnalysisList',
        'loan_count_updated_at': datetime.now(timezone.utc).isoformat(),
    }).eq('id', book_id).execute()
```

기존 `upsert_books_rich_merge` 는 그대로 (NEW 경로에서 사용).

### 7.4 `data4library_collector.py`

기존: usageAnalysisList 호출 후 library_keywords + related_isbns 만 저장.

변경: 추가로 `loan_count`, `loan_count_12mo`, `loan_count_source`, `loan_count_updated_at` 도 UPDATE.

### 7.5 `data4library_discovery_collector.py`

`filter_and_upsert()` 흐름 변경:

```python
for row in rows:
    # usageAnalysisList 로 정확한 loan_count 확보
    usage = fetch_usage_analysis(row['isbn13'])
    row['loan_count'] = usage['loan_count']
    row['loan_count_12mo'] = usage['loan_count_12mo']
    
    action, existing_id = dedup_checker.check(
        row['title'], row['author'], row['isbn13'], row['loan_count']
    )
    if action == DedupAction.UPDATE_LOAN_COUNT:
        update_loan_count_by_book_id(sb, existing_id,
                                     row['loan_count'], row['loan_count_12mo'])
    elif action == DedupAction.NEW:
        upsert_books_rich_merge(sb, [row])
    # SKIP 은 no-op
```

**usageAnalysisList 실패 시**: `usage is None` 이면 해당 row 를 **스킵** (다음 run 재시도).
loanItemSrch 기간값으로 폴백하면 loan_count 소스가 오염되므로 절대 폴백하지 않는다.
또한 `usage['loan_count']` 가 0 이어도 0 은 유효한 누적값이므로 그대로 쓴다 (falsy 폴백 금지).

`sanitize_for_upsert()` 에서 `sales_point: parsed.get('loan_count') or 0` 덮어쓰기 **버그 제거**.

### 7.6 `scripts/backfill_loan_count_unify.py` (신규)

기존 2,700권 대상 1회성 재동기화.

```python
from datetime import datetime, timezone

books = sb.table('books').select('id, isbn, title, author, loan_count').execute().data
for b in books:
    try:
        usage = fetch_usage_analysis(b['isbn'])
        sb.table('books').update({
            'loan_count': usage['loan_count'],
            'loan_count_12mo': usage['loan_count_12mo'],
            'loan_count_source': 'usageAnalysisList',
            'loan_count_updated_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', b['id']).execute()
    except Exception as e:
        print(f"✗ {b['isbn']}: {e}")
    time.sleep(0.3)
```

예상 소요: 2,700 × 0.3s = ~15분.

**backfill 이 에디션 중복을 해소하지는 않음** — DB 에 이미 같은 작품의 두 ISBN 이 들어있으면 둘 다 각자 loan_count 갱신만 받음. 중복 자체는 `fallback_curation.refresh()` 의 `DISTINCT ON (title)` 가 display 단에서 처리. 이후 신규 수집부터 새 dedup 규칙이 적용되어 점진 수렴.

---

## 8. Migration

`supabase/migrations/20260416_loan_count_hybrid.sql`:

```sql
BEGIN;

ALTER TABLE books
  ADD COLUMN IF NOT EXISTS loan_count_12mo INT,
  ADD COLUMN IF NOT EXISTS loan_count_source TEXT,
  ADD COLUMN IF NOT EXISTS loan_count_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_books_loan_count_12mo
  ON books (loan_count_12mo DESC NULLS LAST);

-- refresh_fallback_curation() 재작성 (Strategy C)
CREATE OR REPLACE FUNCTION refresh_fallback_curation() RETURNS void AS $$
BEGIN
  DELETE FROM fallback_curation;
  
  INSERT INTO fallback_curation (rank, book_id, loan_count, added_at)
    WITH d4l AS (
      SELECT DISTINCT ON (title) id, title, loan_count_12mo, loan_count
      FROM books
      WHERE loan_count_12mo IS NOT NULL
        AND title IS NOT NULL
      ORDER BY title, loan_count_12mo DESC NULLS LAST
    ),
    d4l_top AS (
      SELECT id, title, loan_count_12mo AS sort_val, loan_count, 1 AS priority
      FROM d4l ORDER BY loan_count_12mo DESC LIMIT 20
    ),
    -- NOT EXISTS 사용. NOT IN + NULL title 조합은 전체 조건을 UNKNOWN 으로
    -- 만들어 알라딘 보완분이 통째로 빠지는 함정이 있어 회피.
    aladin_new AS (
      SELECT b.id, b.sales_point AS sort_val, b.loan_count, 2 AS priority
      FROM books b
      WHERE b.sales_point IS NOT NULL AND b.sales_point > 0
        AND b.title IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM d4l_top dt WHERE dt.title = b.title)
      ORDER BY b.sales_point DESC LIMIT 10
    ),
    combined AS (
      SELECT id, sort_val, loan_count, priority FROM d4l_top
      UNION ALL
      SELECT id, sort_val, loan_count, priority FROM aladin_new
    )
    SELECT
      ROW_NUMBER() OVER (ORDER BY priority, sort_val DESC NULLS LAST),
      id, loan_count, NOW()
    FROM combined LIMIT 30;
END;
$$ LANGUAGE plpgsql;

COMMIT;
```

---

## 9. Phase 1B runbook 영향

PR #20 bootstrap 후 추가 스텝:

```
1. Secrets 추가 (기존)
2. Apply migrations (20260416 포함)
3. 🆕 python3 scripts/backfill_loan_count_unify.py  (~15분)
4. Seed workflows (기존)
5. SQL: SELECT refresh_fallback_curation();   ← backfill 후 실행
6. e2e 검증
```

backfill 이 fallback refresh **전에** 와야 정보나루 데이터 기반 랭킹이 나옴.

---

## 10. 구현 체크리스트

- [ ] Migration: `20260416_loan_count_hybrid.sql`
- [ ] `lib/data4library_api.py` — parse_usage_analysis 확장
- [ ] `lib/dedup_checker.py` — DedupAction enum + check() 메서드
- [ ] `lib/books_upsert.py` — update_loan_count_by_book_id()
- [ ] `data4library_collector.py` — usageAnalysisList 호출 시 loan_count 저장
- [ ] `data4library_discovery_collector.py` — dedup 경로 분기 + sales_point 버그 제거
- [ ] `scripts/backfill_loan_count_unify.py` (신규)
- [ ] 테스트: dedup_checker UPDATE 경로, usageAnalysisList parser
- [ ] `docs/superpowers/specs/2026-03-20-batch-collection-strategy-design.md` 갱신
- [ ] `docs/superpowers/specs/2026-03-26-onboarding-design.md` 갱신
- [ ] `docs/superpowers/specs/2026-04-07-curation-system-design.md` 갱신
- [ ] `docs/ARCHITECTURE.md` 갱신
- [ ] Phase 1B runbook (`docs/superpowers/plans/phase-1b-eden-runbook.md`) 에 backfill 스텝 추가

---

## 11. 범위 외 (후속)

- **loan_count_source = 'loanItemSrch' 인 책** 을 점진적으로 'usageAnalysisList' 로 갱신하는 daily 배치 — 지금은 1회 backfill 만.
- **loan_count 쇠퇴 반영** — 오래된 loan_count_updated_at 인 책 재수집. Phase 2.
- **loan_groups / maniaRecBooks / readerRecBooks 활용** — usageAnalysisList 응답에 있지만 지금은 저장 안 함. 필요해지면 그때 컬럼 추가.
- **혼합 가중치 튜닝** — 출시 후 CTR 데이터 기반 조정.

---

## 12. 검증 근거 (2026-04-16 실측)

### 데이터 소스 겹침
- 정보나루 loanItemSrch top 20 ∩ 알라딘 Bestseller top 10 = **0권**
- 두 소스가 완전히 다른 풀 커버 → Strategy C 최대 효과

### Strategy C 적용 시 fallback 30권 구성 (시뮬레이션)
- 정보나루 18권 (문학 위주): 한강 3권, 양귀자, 정해연, 김호연 2권, 김영하 2권, 김애란 2권, 김금희 2권, 외
- 알라딘 10권 (인문/자기계발): 프로젝트 헤일메리, 괴테는 모든 것을, 유시민, 신영준 외
- 신간 (2024~2026): 18/28 (64%)

### 정보나루 신간 반영 실측
- 주요 신간 6~12개월 내 반영됨: 혼모노(2025), 단 한 번의 삶(2025), 이중 하나는 거짓말(2024) 등 top 20 진입
- 완전 초기 신간 (출간 직후) 만 지연 → 알라딘으로 커버

### loan_count 통일 효과
- usageAnalysisList.book.loanCnt: 소스 일관성 확보 (기존 loanItemSrch 기간별 혼재 문제 해결)
- loan_count_12mo 추가로 스테디셀러/신간 균형 랭킹 가능
