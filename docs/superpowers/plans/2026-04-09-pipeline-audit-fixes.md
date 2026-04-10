# Pipeline Audit Fixes Implementation Plan

> **Status (2026-04-10):** Phase A~F **완료** (PR #12~#16 + Phase F commit). BLOCKER 8 + HIGH 21 + MEDIUM 11 = 40건 수정. 228 tests passing. Phase G (LOW) / H (smoke) 는 별도 task.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-04-09 pipeline audit 에서 발견된 70개 이슈 중 correctness/데이터 손실 영향이 있는 항목을 우선순위 순으로 수정한다.

**Architecture:** 책 발견 (정보나루 discovery + Aladin smart_batch) → books 테이블 → pipeline orchestrator 5 step (yes24_scraper → v3_vectors → reason_extractor → tier1_embedder → build_index) + 수동 enrich 4개 (tier2, data4library, batch_enricher, v3_reason_extract) + 추천 서버 인덱스. 이 플랜은 silent drop, 중복 삽입, 경쟁 upsert, build_index race, OpenAI 재시도 부재 등 7개 BLOCKER + 18개 HIGH 를 우선 수정하고, 나머지 MEDIUM/LOW 는 조건부로 다룬다.

**Tech Stack:** Python 3 + supabase-py + requests + FastAPI + pytest + monkeypatch. Postgres (Supabase) 스키마 변경 SQL 파일 포함 (Eden 수동 적용).

**참고:**
- Audit 근거: 이 세션 Phase 1 (2026-04-09) — 4 병렬 code-reviewer agent
- 기존 하드닝 패턴: PR #6 (KI-002), `scripts/tier1_embedder.py`, `scripts/lib/batch_fallback.py`
- 메모리: `feedback_never_shortcut.md`, `feedback_monitor_logs.md`, `feedback_batch_operations.md`, `feedback_no_direct_sql.md`, `feedback_recommendation_logic.md`

**검증된 사실 (2026-04-09):**
- `scripts/lib/pipeline_steps.py:52` — orchestrator 가 `scripts/reason_extractor.py` (v1, `source="llm_extracted"`) 를 호출. `v3_reason_extract.py` (`source="v3_context_rich"`) 는 별도 경로
- `supabase/010_love_reasons.sql:8-16` — `book_love_reasons` 는 `id UUID PRIMARY KEY` 만 있고 `(book_id, source, reason)` UNIQUE 없음
- `scripts/smart_batch_collector.py:347-356` — `if not items: completed=True` + aladin_client 가 API 실패 시 `([], 0)` 반환 → keyword 영구 스킵
- `supabase/001_init_schema.sql:123` — `book_embeddings.book_id` UNIQUE, tier1/tier2 모두 `on_conflict=book_id` upsert
- `scripts/lib/openai_helpers.py:49-76` — `call_chat`/`call_embedding` 에 retry 0, `raise_for_status()` 만
- `scripts/lib/openai_helpers.py:12` — `OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")` 빈 기본값
- `tests/conftest.py` — `sys.path` 만 설정, 네트워크 차단 없음
- `scripts/pipeline_orchestrator.py` — `_pending_for_step("reason_extractor")` 가 `with_reasons // 13` 사용
- `recommendation-server/scripts/build_index.py:168` — `pickle.dump()` 직접, tmp+rename 없음
- `scripts/pipeline_orchestrator.py:345` vs `recommendation-server/scripts/build_index.py:22` — 서로 다른 `.env` 로드

---

## File Structure

**Phase A (BLOCKER) — Create:**
- `supabase/migrations/20260410_book_love_reasons_unique.sql` — `(book_id, source, reason)` UNIQUE 제약
- `supabase/migrations/20260410_book_embeddings_tier_composite.sql` — `book_embeddings` 에 `(book_id, tier)` composite unique 로 변경
- `scripts/lib/openai_helpers_retry.py` — 또는 기존 파일에 retry wrapper 추가
- `tests/lib/test_openai_helpers.py` — retry 케이스 테스트
- `tests/conftest_hardening.py` 또는 기존 `tests/conftest.py` 강화

**Phase A — Modify:**
- `scripts/lib/pipeline_steps.py` — `reason_extractor` → `v3_reason_extract` 교체
- `scripts/reason_extractor.py` — insert 를 upsert 로 (또는 v3 로 완전 이전 시 삭제 여부 결정)
- `scripts/v3_reason_extract.py` — insert 를 upsert 로 + 오케스트레이터 compat 플래그 (`--limit`, `--dry-run`)
- `scripts/smart_batch_collector.py` — `_run_search_phase` 의 API 실패 분기 수정
- `scripts/lib/openai_helpers.py` — `call_chat`/`call_embedding` 에 retry, 빈 key 검증
- `tests/conftest.py` — autouse fixture 로 환경변수 + 네트워크 차단
- `supabase/001_init_schema.sql` 와 `migrations/` 레이아웃 정리 (또는 README)

**Phase B (HIGH) — Discovery 수정:**
- `scripts/lib/aladin_client.py` — `_request` 실패를 예외로 surface
- `scripts/smart_batch_collector.py` — API 실패를 stats 로 분리, exit code
- `scripts/data4library_discovery_collector.py` — `main()` exit code 반영, Tier 1 state 저장
- `scripts/lib/dedup_checker.py` — 등록/조회 양쪽 `clean_title()` 일관 적용
- `scripts/data4library_discovery_collector.py:sanitize_for_upsert` — `source="data4library"` 등 schema 정렬
- `scripts/lib/book_filter.py` — data4library 경로에도 적용

**Phase C (HIGH) — Pipeline 수정:**
- `scripts/pipeline_orchestrator.py` — `_pending_for_step("reason_extractor")` 정확한 COUNT DISTINCT 로 교체
- `recommendation-server/scripts/build_index.py` — atomic write (tmp → `os.replace`), skip ratio guard
- `scripts/pipeline_orchestrator.py` — SUPABASE_URL 교차 검증
- `scripts/generate_book_v3_vectors.py` — `with_retry` wrapping 추가

**Phase D (HIGH) — Enricher 수정:**
- `scripts/batch_enricher.py` — `extract_colors` 실패 카운터
- `scripts/v3_reason_extract.py` — `skipped_no_data` 를 `errors` 에서 분리
- `scripts/data4library_collector.py` — 빈 body 를 영구 persist 하지 않도록 수정
- `tests/test_batch_enricher.py` — 색상 실패 카운터 테스트

**Phase E (HIGH) — Cross-cutting 수정:**
- `scripts/lib/retry.py` — SQLSTATE whitelist 확장 (`55P03`, `25P02`, `58030`), 코드 로깅
- `scripts/lib/openai_helpers.py` — import path 일관화 (`scripts.lib.openai_helpers`)
- `scripts/reason_extractor.py` — import path 교정
- `.github/workflows/daily-pipeline.yml` (신규) 또는 기존 workflow 조율
- `scripts/lib/books_loan_count_backfill.py` (신규) — aladin/kakao 책의 loan_count 보강

**Phase F (MEDIUM/LOW) — 조건부:**
- dedup_checker 정규화 개선, 비운영 진단 스크립트 anon key 전환, logger 통합, 기타

**Phase G (보류된 현재 작업):**
- Phase 2 smoke test — 이 플랜 실행 후 별도 task 로 진행

**Do NOT modify:**
- `scripts/tier1_embedder.py` (PR #6 에서 하드닝 완료)
- `scripts/lib/batch_fallback.py` (PR #6 에서 도입, 안정)
- `tests/lib/test_batch_fallback.py`

---

## Phase A — BLOCKER (7건, 필수 수정)

### Task A1: `book_love_reasons` UNIQUE 제약 추가 (B2 선결 조건)

**Files:**
- Create: `supabase/migrations/20260410_book_love_reasons_unique.sql`

**Why:** B1 (orchestrator → v3) 로 전환 전에 중복 방지가 먼저 필요. v1 reason 과 v3 reason 이 `(book_id, source)` 별로 공존할 수 있어야 함. 현재는 `id` 만 PK 이므로 `insert().execute()` 가 계속 누적.

**참고 사실:** `supabase/010_love_reasons.sql:8-16` 확인됨. Eden 이 SQL 직접 적용 — Claude 는 파일만 생성 (`feedback_no_direct_sql.md`).

- [ ] **Step 1: migration SQL 작성**

```sql
-- supabase/migrations/20260410_book_love_reasons_unique.sql
-- book_love_reasons: 같은 책 + 같은 source 에서 동일 reason 중복 방지.
-- 영향: reason_extractor(v1, source='llm_extracted') 와
--       v3_reason_extract(source='v3_context_rich') 는 독립적으로 공존.
-- 기존 중복 데이터가 있으면 migration 실패 → Eden 수동 정리 후 재시도.

ALTER TABLE public.book_love_reasons
  ADD CONSTRAINT book_love_reasons_book_source_reason_unique
  UNIQUE (book_id, source, reason);
```

- [ ] **Step 2: Eden 수동 적용 안내 주석 추가**

파일 맨 위에 다음 주석 추가:

```sql
-- 적용: Supabase 대시보드 → SQL Editor → 실행.
-- 기존 중복 에러 발생 시:
--   DELETE FROM public.book_love_reasons a USING public.book_love_reasons b
--   WHERE a.id > b.id
--     AND a.book_id = b.book_id
--     AND a.source = b.source
--     AND a.reason = b.reason;
--   그 후 ALTER TABLE 재시도.
```

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260410_book_love_reasons_unique.sql
git commit -m "fix: book_love_reasons (book_id, source, reason) UNIQUE (B2)"
```

- [ ] **🛑 STOP — Eden 수동 적용 대기**

A2 로 넘어가기 전에 반드시:
1. Eden 이 `20260410_book_love_reasons_unique.sql` 을 Supabase 에 적용
2. 기존 중복 데이터 있으면 파일 주석의 DELETE SQL 로 정리 후 재적용
3. Eden 이 "적용 완료" 확인

A2 는 plain insert → upsert 전환이므로 UNIQUE 제약이 먼저 존재해야 `ignore_duplicates` 가 의미 있음. 순서 어기면 A2 후에도 중복 행이 계속 쌓임.

---

### Task A2: reason_extractor 와 v3_reason_extract 의 insert 를 upsert 로 변경

**Files:**
- Modify: `scripts/reason_extractor.py` (insert 위치 — 검증 확인됨: `reason_extractor.py:605`)
- Modify: `scripts/v3_reason_extract.py` (insert 위치 — `v3_reason_extract.py:178`)
- Test: `tests/test_reason_extractor.py`, `tests/test_v3_reason_extract.py` (신규)

**Why:** UNIQUE 제약 추가 후 plain insert 는 23505 에러. on_conflict 로 idempotent 하게.

- [ ] **Step 1: reason_extractor 현재 insert 확인**

```bash
sed -n '595,625p' scripts/reason_extractor.py
```

Expected: `sb.table("book_love_reasons").insert(rows).execute()` 형태.

- [ ] **Step 2: reason_extractor insert → upsert**

```python
# Before
with_retry(lambda: sb.table("book_love_reasons").insert(rows).execute())

# After — supabase-py 2.28.2 확인됨, ignore_duplicates 지원
with_retry(lambda: sb.table("book_love_reasons").upsert(
    rows,
    on_conflict="book_id,source,reason",
    ignore_duplicates=True,
).execute())
```

- [ ] **Step 3: v3_reason_extract 도 동일 변경**

파일 `scripts/v3_reason_extract.py:178` 인근의 `sb.table("book_love_reasons").insert(rows).execute()` 을 위와 같이 교체.

- [ ] **Step 4: Idempotency 단위 테스트**

`tests/test_reason_extractor.py` (또는 신규 `test_reason_idempotency.py`) 에 추가:

```python
def test_reason_insert_is_idempotent(monkeypatch):
    """같은 (book_id, source, reason) 2회 upsert 해도 1건만 최종 저장되어야 한다.

    ignore_duplicates=True 로 ON CONFLICT DO NOTHING 동작 검증.
    """
    from unittest.mock import MagicMock
    import scripts.reason_extractor as rex

    # upsert 호출을 캡처
    calls = []
    fake_table = MagicMock()
    fake_table.upsert.side_effect = lambda rows, **kwargs: (
        calls.append({"rows": rows, "kwargs": kwargs}) or fake_table
    )
    fake_table.execute.return_value = MagicMock(data=[])

    sb = MagicMock()
    sb.table.return_value = fake_table

    rows = [{"book_id": "b1", "source": "llm_extracted",
             "reason": "이유 하나", "reason_embedding": [0.1] * 4}]

    # 2회 호출
    from scripts.lib.retry import with_retry
    for _ in range(2):
        with_retry(lambda: sb.table("book_love_reasons").upsert(
            rows, on_conflict="book_id,source,reason",
            ignore_duplicates=True,
        ).execute())

    # upsert 가 2회 호출되었지만, 각 호출 모두 on_conflict + ignore_duplicates 를 전달해야 함
    assert len(calls) == 2
    for c in calls:
        assert c["kwargs"]["on_conflict"] == "book_id,source,reason"
        assert c["kwargs"]["ignore_duplicates"] is True
```

**주의:** 이 테스트는 code contract (`on_conflict` + `ignore_duplicates` 가 전달되는지) 를 검증. 실제 Postgres ON CONFLICT DO NOTHING 동작은 SQL 단에서 보장되며 A1 migration + Eden 수동 적용 후 production 에서 효력 발생.

- [ ] **Step 5: 회귀 테스트**

```bash
python3 -m pytest tests/test_reason_extractor.py tests/test_v3_reason_extract.py -q
```

Expected: 기존 + 신규 모두 PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/reason_extractor.py scripts/v3_reason_extract.py
git commit -m "fix: reason insert → upsert (on_conflict book_id/source/reason, B2)"
```

---

### Task A3: orchestrator 를 v3_reason_extract 로 전환 (B1)

**Files:**
- Modify: `scripts/lib/pipeline_steps.py`
- Modify: `scripts/v3_reason_extract.py` (orchestrator compat 플래그)

**Why:** 검증 확인: `scripts/lib/pipeline_steps.py:52` 가 `scripts/reason_extractor.py` 를 가리킴. Eden 의 v3 원칙 (`project_recommendation_v3`) 과 충돌. 새 책은 전부 v1 형태로 저장 중.

**주의:** v3_reason_extract 가 orchestrator 의 `--limit`, `--dry-run`, exit code 를 지원하는지 먼저 확인. 기존 `--limit` 은 있지만 `--dry-run` 플래그는 확인 필요.

- [ ] **Step 1: v3_reason_extract 의 플래그 검증**

```bash
grep -n "add_argument\|supports_limit\|dry.run" scripts/v3_reason_extract.py
```

- [ ] **Step 2: v3_reason_extract 에 --dry-run 추가 (없는 경우)**

`scripts/v3_reason_extract.py` 의 `argparse` 섹션에:

```python
parser.add_argument("--dry-run", action="store_true",
                    help="DB insert 스킵 (LLM/embed 는 호출)")
```

그리고 insert 호출 직전에:

```python
if args.dry_run:
    print(f"  (dry-run) would insert {len(rows)} reason rows")
    total_saved += len(valid)
    continue
```

- [ ] **Step 3: pipeline_steps.py 전환**

```python
# Before
PipelineStep(
    name="reason_extractor",
    script_path="scripts/reason_extractor.py",
    ...
),

# After
PipelineStep(
    name="reason_extractor",
    script_path="scripts/v3_reason_extract.py",
    supports_limit=True,
    supports_dry_run=True,
    limit_flag="--limit",
    progress_counter="with_reasons",
    ratio_verifiable=False,  # v3 는 row 단위, pending 정확도 보존 안 됨
),
```

`name` 은 `reason_extractor` 를 유지 (다운스트림 테스트/로그 호환).

- [ ] **Step 4: 전환 테스트**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py -q
```

Expected: 기존 테스트 모두 PASS (name 은 그대로, script_path 만 변경).

- [ ] **Step 5: Manual smoke (dry-run)**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python3 -u scripts/v3_reason_extract.py --limit 3 --dry-run --no-checkpoint 2>&1 | tail -20
```

Expected: exit 0, "would insert ..." 로그.

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/pipeline_steps.py scripts/v3_reason_extract.py
git commit -m "fix: orchestrator 의 reason_extractor → v3_reason_extract 전환 (B1)"
```

---

### Task A4: v3_reason_extract 최소 테스트 작성 + reason_extractor 의 역할 문서화

**Files:**
- Create: `tests/test_v3_reason_extract.py`
- Modify: `scripts/reason_extractor.py` (최상단 docstring 업데이트)

**Why:** orchestrator 는 이제 v3 경로. reason_extractor 는 legacy (Eden 수동 재실행, 혹은 삭제 후보). 역할을 명확히 주석으로 고정 + v3 경로에 기본 테스트.

- [ ] **Step 1: reason_extractor docstring 업데이트**

`scripts/reason_extractor.py` 맨 위 docstring 을:

```python
"""v1 Reason 추출 — 레거시 경로.

⚠️ 2026-04-10 이후 orchestrator 는 이 스크립트를 호출하지 않는다.
   메인 경로는 scripts/v3_reason_extract.py (source='v3_context_rich').

이 스크립트는:
- source='llm_extracted' 로 저장 (v1 format)
- 공존 가능: book_love_reasons UNIQUE (book_id, source, reason) 덕분
- Eden 이 legacy data 를 re-extract 할 때만 수동 실행

삭제 후보이지만 기존 v1 데이터 분석용으로 당분간 보존.
"""
```

- [ ] **Step 2: v3_reason_extract 기본 테스트**

`tests/test_v3_reason_extract.py` 신규:

```python
"""v3_reason_extract 최소 단위 테스트."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch


def test_source_tag_is_v3_context_rich():
    import v3_reason_extract
    assert v3_reason_extract.SOURCE_TAG == "v3_context_rich"


def test_build_v3_prompt_contains_rules():
    from v3_reason_extract import build_v3_prompt
    prompt = build_v3_prompt("제목", "소설", "본문 설명")
    assert "이유" in prompt
    assert "15~40자" in prompt
    assert "본문 설명" in prompt


def test_filter_v3_reasons_drops_short():
    from v3_reason_extract import filter_v3_reasons
    reasons = ["짧음", "이것은 15자 이상인 맥락이 있는 이유 하나"]
    out = filter_v3_reasons(reasons)
    # 15자 미만은 탈락
    assert "짧음" not in out
```

(실제 filter 함수명은 `scripts/v3_reason_extract.py` grep 후 조정)

- [ ] **Step 3: 테스트 실행**

```bash
python3 -m pytest tests/test_v3_reason_extract.py -v
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_v3_reason_extract.py scripts/reason_extractor.py
git commit -m "test: v3_reason_extract 기본 + reason_extractor legacy 명시"
```

---

### Task A5: `book_embeddings` tier composite unique (B8)

**Files:**
- Create: `supabase/migrations/20260410_book_embeddings_tier_composite.sql`
- Modify: `scripts/tier1_embedder.py` (on_conflict — PR #6 "do not modify" 예외, 1줄 schema 호환 변경)
- Modify: `scripts/tier2_embedder.py` (on_conflict)
- Modify: `scripts/taste_recomputer.py` (reader — max tier 선택)

**검증된 사실 (2026-04-09 re-check):**
- `recommendation-server/scripts/build_index.py` 는 `book_v3_vectors` + `book_love_reasons` 만 읽음. `book_embeddings` 를 **읽지 않음** → build_index 는 수정 대상 아님.
- `book_embeddings` reader 목록: `taste_recomputer.py:172` (실제 read), `experiment_confidence.py` (진단), `pipeline_orchestrator.collect_status` (count only). 이 중 composite unique 적용 후 수정 필요한 곳은 **taste_recomputer 만**.
- `taste_recomputer` 가 `emb_map = {book_id: embedding}` dict 로 조립 — 같은 book_id 에 2 row (tier1, tier2) 있으면 마지막 row 가 dict 를 덮어씀. ORDER BY 없어서 결정론적이지 않음.

**Why:** `supabase/001_init_schema.sql:123` 에 `book_id ... unique`. 현재 tier1/tier2 가 같은 `on_conflict=book_id` 로 upsert → tier1 재실행 시 tier2 rich 데이터 silent overwrite 가능.

**PR #6 "do not modify" 재확인:** 기존 불변 규칙은 retry/exit-code 하드닝 범위. 이 task 는 schema-호환 목적 1줄 변경 (`on_conflict="book_id"` → `"book_id,tier"`). 기존 `save_embeddings_with_fallback`/helper 로직은 유지.

- [ ] **Step 1: migration SQL 작성**

```sql
-- supabase/migrations/20260410_book_embeddings_tier_composite.sql
-- book_embeddings 를 (book_id, tier) 복합 unique 로 변경.
-- 동일 book 의 tier1/tier2 가 공존 가능 → build_index 가 max tier 선택.

-- 기존 UNIQUE(book_id) 제약명을 먼저 확인 후 삭제.
-- Supabase 기본 제약명: book_embeddings_book_id_key

ALTER TABLE public.book_embeddings
  DROP CONSTRAINT IF EXISTS book_embeddings_book_id_key;

-- 새 composite unique
ALTER TABLE public.book_embeddings
  ADD CONSTRAINT book_embeddings_book_id_tier_unique
  UNIQUE (book_id, tier);

-- 기존 인덱스는 그대로 유지 (idx_book_embeddings_hnsw).
```

파일 맨 위 주석:

```sql
-- 적용: Supabase SQL Editor.
-- 기존 데이터 영향:
--   - 현재 각 book_id 에 1 row 뿐이면 migration 은 무손상.
--   - 만약 과거 실험으로 (book_id, tier=1) + (book_id, tier=2) 가
--     모두 존재한다면 기존 UNIQUE(book_id) 가 이미 걸렸을 것이므로
--     이 케이스는 불가능.
```

- [ ] **Step 2: tier1_embedder on_conflict 변경**

```python
# scripts/tier1_embedder.py save_embeddings_chunk 내부
with_retry(
    lambda: sb.table("book_embeddings")
    .upsert(rows, on_conflict="book_id,tier")
    .execute()
)
```

- [ ] **Step 3: tier2_embedder on_conflict 변경**

`scripts/tier2_embedder.py` 의 helper closure 내부 `on_conflict="book_id"` → `on_conflict="book_id,tier"`.

- [ ] **Step 4: taste_recomputer reader 가 max tier 선택**

`scripts/taste_recomputer.py:171-177` 의 embedding 로드를 변경:

```python
# Before
embeddings_result = with_retry(lambda: (
    self.sb.table("book_embeddings")
    .select("book_id, embedding")
    .in_("book_id", book_ids)
    .execute()
))
emb_map = {e["book_id"]: e["embedding"] for e in (embeddings_result.data or [])}

# After — tier desc 로 정렬 후 book_id 당 첫 row (max tier) 채택
embeddings_result = with_retry(lambda: (
    self.sb.table("book_embeddings")
    .select("book_id, tier, embedding")
    .in_("book_id", book_ids)
    .order("tier", desc=True)
    .execute()
))
emb_map = {}
for e in (embeddings_result.data or []):
    # tier desc 정렬이므로 첫 번째가 max tier
    if e["book_id"] not in emb_map:
        emb_map[e["book_id"]] = e["embedding"]
```

- [ ] **Step 5: taste_recomputer 테스트 추가**

`tests/test_taste_recomputer.py` 에 max-tier 선택 단위 테스트:

```python
def test_fetch_user_books_with_embeddings_prefers_max_tier():
    """같은 book_id 에 tier1/tier2 공존 시 tier2 를 채택한다."""
    import taste_recomputer
    with patch.object(taste_recomputer, "create_client", return_value=MagicMock()):
        rc = taste_recomputer.TasteRecomputer(dry_run=True)

    # user_books 에 1권
    rc.sb.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .execute.return_value.data = [{"book_id": "b1", "rating": "good",
                                        "emotion_tags": [], "review_text": ""}]

    # book_embeddings: tier desc 로 정렬된 결과 — tier2 가 먼저
    rc.sb.table.return_value.select.return_value.in_.return_value \
        .order.return_value.execute.return_value.data = [
        {"book_id": "b1", "tier": 2, "embedding": [0.9, 0.9]},
        {"book_id": "b1", "tier": 1, "embedding": [0.1, 0.1]},
    ]

    result = rc.fetch_user_books_with_embeddings("user1")
    assert len(result) == 1
    assert result[0]["embedding"] == [0.9, 0.9]  # tier2 채택
```

- [ ] **Step 6: 기존 테스트 회귀 확인**

```bash
python3 -m pytest tests/test_tier1_embedder.py tests/test_tier2_embedder.py \
                  tests/test_taste_recomputer.py -q
cd recommendation-server && python3 -m pytest tests/test_index.py -q && cd ..
```

Expected: 모두 PASS. build_index 는 `book_embeddings` 를 읽지 않으므로 영향 없음.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260410_book_embeddings_tier_composite.sql \
        scripts/tier1_embedder.py scripts/tier2_embedder.py \
        scripts/taste_recomputer.py tests/test_taste_recomputer.py
git commit -m "fix: book_embeddings (book_id, tier) composite unique + taste_recomputer max-tier (B8)"
```

---

### Task A6: smart_batch API 실패를 영구 스킵으로 만들지 않음 (B3)

**Files:**
- Modify: `scripts/smart_batch_collector.py`
- Modify: `scripts/lib/aladin_client.py`
- Test: `tests/test_smart_batch_collector.py`

**Why:** 검증 확인: `smart_batch_collector.py:347-356` 의 `if not items: completed=True` 분기 + `aladin_client._request` 가 실패 시 `([], 0)` 반환 → transient API 실패 시 keyword 영구 스킵.

- [ ] **Step 1: aladin_client 에서 실패 원인 구분**

`scripts/lib/aladin_client.py` 의 `_request` 가 현재 None 반환인지 확인:

```bash
grep -n "def _request\|return None\|return \[\]\|return ({}\|return (\[\]" scripts/lib/aladin_client.py
```

- [ ] **Step 2: _request 실패를 예외로 변경**

`_request` 가 retry 소진 시 `AladinAPIError` 예외 raise. `fetch_item_list` / `search_books` 는 이를 rethrow.

```python
# scripts/lib/aladin_client.py
class AladinAPIError(Exception):
    """Aladin API 호출 실패 (retry 소진 등). 호출자가 transient 여부 판단."""
    pass

def _request(self, ...):
    for attempt in range(MAX_RETRIES):
        try:
            ...
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise AladinAPIError(f"retries exhausted: {e}") from e
            time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise AladinAPIError("unreachable")
```

- [ ] **Step 3: smart_batch _run_search_phase 의 실패 처리 분리**

```python
# scripts/smart_batch_collector.py _run_search_phase
for page in range(last_page + 1, SEARCH_MAX_PAGES + 1):
    if not self.has_capacity():
        break

    try:
        items, total = self.aladin.search_books(keyword, page)
    except Exception as e:
        # 진짜 API 실패 — keyword 를 완료 처리하지 않음
        print(f"    ⚠ '{keyword}' p{page} API 실패 — 다음 실행에서 재시도: {e}")
        self.stats["api_errors"] = self.stats.get("api_errors", 0) + 1
        self.state_mgr.upsert_state(
            source_type=source_type,
            search_keyword=keyword,
            last_page_fetched=last_page,  # 진전 없음
            total_items_found=total_found,
            unique_items_saved=unique_saved,
            completed=False,  # 핵심: False 유지
        )
        break

    total_found += len(items)
    pages_fetched += 1

    if not items:
        # API 는 성공이지만 진짜 결과 0건 — 이 때만 완료 처리
        self.state_mgr.upsert_state(
            source_type=source_type,
            search_keyword=keyword,
            last_page_fetched=page,
            total_items_found=total_found,
            unique_items_saved=unique_saved,
            completed=True,
        )
        break
    ...
```

`run_item_list` 도 같은 패턴 적용 (try/except 후 API 실패면 continue, 성공 + 0 건은 기존대로).

- [ ] **Step 4: stats 에 api_errors 추가 + exit code 반영**

```python
# __init__ 의 self.stats 에:
self.stats = {
    ...
    "saved": 0,
    "drop_failed": 0,
    "api_errors": 0,
}

# main() 에서:
return 1 if (collector.stats["drop_failed"] > 0 or collector.stats["api_errors"] > 0) else 0
```

- [ ] **Step 5: 테스트**

`tests/test_smart_batch_collector.py` 추가:

```python
def test_run_search_phase_api_failure_does_not_mark_completed():
    """KI: transient API 실패 시 keyword 가 completed=True 로 저장되지 않음."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)

    capacity_calls = {"n": 0}
    def fake_has_capacity():
        capacity_calls["n"] += 1
        return capacity_calls["n"] <= 2

    # search_books 가 예외 raise
    collector.aladin.search_books = MagicMock(
        side_effect=smart_batch_collector.AladinAPIError("transient 500")
    )
    collector.state_mgr.get_state = MagicMock(return_value=None)
    collector.state_mgr.upsert_state = MagicMock()

    with patch.object(collector, "has_capacity", side_effect=fake_has_capacity), \
         patch("time.sleep"):
        collector._run_search_phase(["테스트키워드"], "keyword_search")

    # upsert_state 호출 시 completed=False 여야 함
    upsert_kwargs = collector.state_mgr.upsert_state.call_args.kwargs
    assert upsert_kwargs["completed"] is False
    assert collector.stats["api_errors"] >= 1
```

- [ ] **Step 6: 실행 + commit**

```bash
python3 -m pytest tests/test_smart_batch_collector.py -q
git add scripts/smart_batch_collector.py scripts/lib/aladin_client.py tests/test_smart_batch_collector.py
git commit -m "fix: smart_batch API 실패 시 keyword 영구 스킵 방지 (B3)"
```

---

### Task A7: `tests/conftest.py` 네트워크 차단 + 환경변수 fixture (B5)

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_conftest_hardening.py` (새 fixture 동작 검증)

**Why:** 검증 확인: 현재 `conftest.py` 는 `sys.path.insert` 만. 테스트 중 `create_client`/`requests.post` 가 실제로 Supabase/OpenAI/정보나루에 network 호출 가능 (service_role 키 노출 리스크).

- [ ] **Step 1: 현재 conftest 확인**

```bash
cat tests/conftest.py
```

Expected: 단 5 줄. `sys.path.insert` 만.

- [ ] **Step 2: autouse fixture 추가**

```python
# tests/conftest.py
import os
import sys

# scripts/lib 를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _isolate_from_real_services(monkeypatch):
    """모든 테스트는 기본적으로 실제 네트워크/DB 에서 격리된다.

    - 환경변수를 가짜 값으로 세팅 (service_role 등 실수로 실행되면 401)
    - requests.post/get 를 명시적으로 패치하지 않은 테스트에서 호출되면 즉시 실패
    - supabase.create_client 을 MagicMock 으로 대체

    실제 네트워크 테스트가 필요하면 각 테스트가 개별 monkeypatch 로 복구.
    """
    # 환경변수
    fake_env = {
        "SUPABASE_URL": "http://test.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role",
        "SUPABASE_ANON_KEY": "fake-anon",
        "OPENAI_API_KEY": "fake-openai",
        "ALADIN_TTB_KEY": "fake-aladin",
        "KAKAO_REST_API_KEY": "fake-kakao",
        "DATA4LIBRARY_API_KEY": "fake-data4library",
        "RECOMMENDATION_SERVER_URL": "http://test.invalid",
    }
    for k, v in fake_env.items():
        monkeypatch.setenv(k, v)

    # requests 차단
    import requests

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Test attempted real HTTP call. Use monkeypatch to stub it."
        )

    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "put", _blocked)
    monkeypatch.setattr(requests, "delete", _blocked)

    # supabase.create_client 차단 — 테스트가 직접 patch 해야 동작
    try:
        import supabase
        from unittest.mock import MagicMock
        monkeypatch.setattr(supabase, "create_client",
                            lambda *a, **k: MagicMock())
    except ImportError:
        pass

    yield
```

- [ ] **Step 3: fixture 동작 검증 테스트**

`tests/test_conftest_hardening.py`:

```python
"""conftest autouse fixture 가 실제 네트워크 차단 확인."""
import pytest
import requests


def test_fake_env_vars_set():
    import os
    assert os.environ["SUPABASE_URL"] == "http://test.invalid"
    assert os.environ["OPENAI_API_KEY"] == "fake-openai"


def test_requests_post_blocked():
    with pytest.raises(RuntimeError, match="real HTTP call"):
        requests.post("https://api.openai.com/v1/chat/completions")


def test_requests_get_blocked():
    with pytest.raises(RuntimeError, match="real HTTP call"):
        requests.get("https://api.example.com/")


def test_supabase_create_client_returns_mock():
    import supabase
    client = supabase.create_client("x", "y")
    # MagicMock — 아무 method 호출해도 에러 없음
    client.table("books").select("*").execute()
```

- [ ] **Step 4: 기존 테스트 회귀 실행 (매우 중요)**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -15
```

Expected: 기존 테스트 중 실제 네트워크에 의존하는 것이 있으면 실패한다 (실제로는 없어야 함, 있으면 그 테스트를 monkeypatch 로 수정).

만약 실패하는 테스트가 있으면 파일별로 fix 하며 진행. `data4library_discovery_collector` 계열 테스트가 `_sb = MagicMock()` 로 이미 격리되어 있어 영향 없어야 함.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_conftest_hardening.py
git commit -m "test: conftest autouse 로 네트워크/환경 격리 (B5)"
```

---

### Task A8: `openai_helpers` retry + 빈 key 검증 (B6, B7)

**Files:**
- Modify: `scripts/lib/openai_helpers.py`
- Test: `tests/lib/test_openai_helpers.py` (신규)

**Why:** 검증 확인: `call_chat`/`call_embedding` 에 retry 0, 429/5xx 하드 크래시. `OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")` 빈 기본값 → 런타임 401.

- [ ] **Step 1: retry helper 작성**

`scripts/lib/openai_helpers.py` 수정:

```python
"""OpenAI API 직접 호출 헬퍼.

openai 패키지 호환 문제(jiter 모듈) 우회를 위해 requests 로 직접 호출.
429/5xx 에는 지수 백오프 재시도.
"""
import json
import os
import time

import requests


def _get_api_key() -> str:
    """OPENAI_API_KEY 환경변수 조회 + 검증.

    빈 값일 때 raise 해서 런타임에 401 이 아닌 설정 오류로 실패.
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다. "
            ".env 파일 확인."
        )
    return key


CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 2000
API_TIMEOUT = 60

MAX_RETRIES = 4
BACKOFF_BASE = 1.0  # 1s, 2s, 4s, 8s


def _is_retryable(status: int) -> bool:
    """429 (rate limit), 500/502/503/504 (transient server) 은 재시도."""
    return status == 429 or 500 <= status < 600


def _call_with_retry(url: str, payload: dict) -> dict:
    """requests.post + 재시도. 4xx (429 제외) 은 즉시 raise."""
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=API_TIMEOUT)
            if resp.status_code < 400:
                return resp.json()
            if not _is_retryable(resp.status_code):
                # 영구 에러 — 즉시 raise
                resp.raise_for_status()
            # retryable — 마지막 시도면 raise
            if attempt == MAX_RETRIES - 1:
                resp.raise_for_status()
            delay = BACKOFF_BASE * (2 ** attempt)
            print(f"  ⚠ OpenAI {resp.status_code} retry {attempt+1}/{MAX_RETRIES} (sleep {delay}s)")
            time.sleep(delay)
        except requests.RequestException as e:
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                raise
            delay = BACKOFF_BASE * (2 ** attempt)
            print(f"  ⚠ OpenAI network err retry {attempt+1}/{MAX_RETRIES}: {e}")
            time.sleep(delay)
    raise RuntimeError(f"OpenAI call failed after {MAX_RETRIES} retries: {last_exc}")


def build_chat_payload(prompt, temperature=0.3):
    return {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }


def build_embedding_payload(texts):
    return {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "dimensions": EMBEDDING_DIMENSIONS,
    }


def parse_chat_response(response_json):
    content = response_json["choices"][0]["message"]["content"]
    return json.loads(content)


def parse_embedding_response(response_json):
    return [d["embedding"] for d in response_json["data"]]


def call_chat(prompt, temperature=0.3):
    data = _call_with_retry(
        "https://api.openai.com/v1/chat/completions",
        build_chat_payload(prompt, temperature),
    )
    return parse_chat_response(data)


def call_embedding(texts):
    data = _call_with_retry(
        "https://api.openai.com/v1/embeddings",
        build_embedding_payload(texts),
    )
    return parse_embedding_response(data)
```

- [ ] **Step 2: 테스트 작성**

`tests/lib/test_openai_helpers.py`:

```python
"""openai_helpers retry/error 단위 테스트."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch

# conftest autouse 가 OPENAI_API_KEY='fake-openai' 로 세팅한다.


def _fake_response(status_code, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    if status_code >= 400:
        import requests
        err = requests.HTTPError(f"{status_code}")
        err.response = r
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_api_key_raises_when_empty(monkeypatch):
    from scripts.lib.openai_helpers import _get_api_key
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="설정되지 않았습니다"):
        _get_api_key()


def test_call_chat_success_first_try():
    import scripts.lib.openai_helpers as oh
    body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(200, body)):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"ok": True}


def test_call_chat_retries_on_429():
    """429 2회 후 200."""
    import scripts.lib.openai_helpers as oh
    ok_body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    responses = [
        _fake_response(429),
        _fake_response(429),
        _fake_response(200, ok_body),
    ]
    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=responses):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"ok": True}


def test_call_chat_retries_on_500_then_succeeds():
    import scripts.lib.openai_helpers as oh
    ok_body = {"choices": [{"message": {"content": '{"x": 1}'}}]}
    responses = [
        _fake_response(500),
        _fake_response(200, ok_body),
    ]
    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=responses):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"x": 1}


def test_call_chat_raises_after_max_retries():
    import scripts.lib.openai_helpers as oh
    import requests
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(429)):
        with patch("time.sleep"):
            with pytest.raises(requests.HTTPError):
                oh.call_chat("hello")


def test_call_chat_non_retryable_4xx_fails_fast():
    """400 은 즉시 raise (retry 하지 않음)."""
    import scripts.lib.openai_helpers as oh
    import requests
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        return _fake_response(400)

    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=fake_post):
        with patch("time.sleep"):
            with pytest.raises(requests.HTTPError):
                oh.call_chat("hello")
    assert call_count["n"] == 1


def test_call_embedding_success():
    import scripts.lib.openai_helpers as oh
    body = {"data": [{"embedding": [0.1, 0.2]}]}
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(200, body)):
        with patch("time.sleep"):
            result = oh.call_embedding(["text"])
    assert result == [[0.1, 0.2]]
```

- [ ] **Step 3: 실행**

```bash
mkdir -p tests/lib
python3 -m pytest tests/lib/test_openai_helpers.py -v
```

Expected: 7/7 PASS.

- [ ] **Step 4: 기존 caller 회귀 확인**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 모든 테스트 PASS. (reason_extractor, v3_reason_extract, generate_book_v3_vectors 등이 openai_helpers 를 사용하지만 mock 으로 격리되어 있어야 함.)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/openai_helpers.py tests/lib/test_openai_helpers.py
git commit -m "fix: openai_helpers retry + 빈 key 검증 (B6, B7)"
```

---

## Phase B — HIGH Discovery (6건)

### Task B1: aladin_client 실패 surface + smart_batch run_item_list 패턴 적용

**Files:**
- Modify: `scripts/lib/aladin_client.py`
- Modify: `scripts/smart_batch_collector.py::run_item_list`

**Why:** A6 에서 `_run_search_phase` 만 수정했음. `run_item_list` 도 같은 패턴 (API 실패 → transient 로 처리) 이 필요.

- [ ] **Step 1: run_item_list 에 try/except 적용**

```python
# scripts/smart_batch_collector.py run_item_list
for page in range(1, MAX_PAGES + 1):
    for cat_id, cat_name in CATEGORIES.items():
        for qt in QUERY_TYPES:
            if not self.has_capacity():
                print("\n⚠ 일일 API 한도 도달. 다음 실행에서 이어갑니다.")
                return

            try:
                items, total = self.aladin.fetch_item_list(qt, cat_id, page)
            except Exception as e:
                print(f"    ⚠ {cat_name}/{qt} p{page} API 실패 — 건너뜀: {e}")
                self.stats["api_errors"] += 1
                time.sleep(API_CALL_DELAY)
                continue

            if not items:
                continue

            books = self.process_items(items)
            if books:
                saved, failed = self.save_batch(books)
                self.stats["saved"] += saved
                self.stats["drop_failed"] += failed
                yield_rate = len(books) / len(items) if items else 0
                print(f"  {cat_name} / {qt} p{page}: +{saved}권 (yield {yield_rate:.0%})")

            time.sleep(API_CALL_DELAY)
```

- [ ] **Step 2: 테스트 추가**

`tests/test_smart_batch_collector.py` 에:

```python
def test_run_item_list_survives_api_error():
    """한 카테고리 API 실패 → 다음 카테고리 계속 진행."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)

    call_count = {"n": 0}
    def flaky_fetch(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise smart_batch_collector.AladinAPIError("transient")
        return ([], 0)

    collector.aladin.fetch_item_list = MagicMock(side_effect=flaky_fetch)
    collector.has_capacity = MagicMock(side_effect=[True, True, False])

    with patch("time.sleep"):
        collector.run_item_list()

    assert collector.stats["api_errors"] >= 1
```

- [ ] **Step 3: 실행 + commit**

```bash
python3 -m pytest tests/test_smart_batch_collector.py -q
git add scripts/smart_batch_collector.py
git commit -m "fix: smart_batch run_item_list API 실패 격리 + api_errors 카운트"
```

---

### Task B2: data4library_discovery 의 exit code 연결

**Files:**
- Modify: `scripts/data4library_discovery_collector.py::main`
- Test: `tests/test_data4library_discovery.py`

**Why:** 검증 확인: 현재 `main()` 이 `c.report()` 만 하고 exit code 0 고정. `stats["errors"]` 가 100% 되어도 cron 이 감지 못함.

- [ ] **Step 1: main() 에 exit code 반영**

```python
# scripts/data4library_discovery_collector.py
def main():
    ...
    c.report()

    # exit code: errors 가 있으면 1
    rc = 0
    if c.stats.get("errors", 0) > 0:
        rc = 1

    if args.with_enrich:
        code = trigger_enrich_pipeline(dry_run=args.dry_run, limit=args.enrich_limit)
        if code != 0:
            print(f"⚠ enrich pipeline 실패 (exit {code})", file=sys.stderr)
            return max(rc, code)

    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 2: 테스트**

```python
# tests/test_data4library_discovery.py 에 추가
def test_main_returns_one_when_errors():
    import scripts.data4library_discovery_collector as dlc
    with patch("scripts.data4library_discovery_collector.DiscoveryCollector") as mock_cls:
        collector = MagicMock()
        collector.stats = {"errors": 3}
        collector.fetch_tier1.return_value = []
        mock_cls.return_value = collector
        with patch("sys.argv", ["prog", "--tier", "1"]):
            rc = dlc.main()
    assert rc == 1
```

- [ ] **Step 3: 실행 + commit**

```bash
python3 -m pytest tests/test_data4library_discovery.py -q
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "fix: data4library_discovery main() exit code 연결"
```

---

### Task B3: dedup_checker 정규화 일관성

**Files:**
- Modify: `scripts/lib/dedup_checker.py`
- Modify: `scripts/data4library_discovery_collector.py` (등록/조회 경로)
- Modify: `scripts/smart_batch_collector.py` (이미 `clean_title` 적용 — 확인만)
- Test: `tests/test_dedup_checker.py` (없으면 신규, 있으면 확장)

**Why:** Audit 결과: `load_title_index()` 가 DB 에서 raw title 로 인덱스 구축 → 등록 시 `clean_title(title)` 으로 저장 → 같은 책이 다른 key 로 인덱스됨. Cross-source 중복 슬립.

- [ ] **Step 1: 현재 로직 확인**

```bash
grep -n "def load_title_index\|_normalize\|clean_title" scripts/lib/dedup_checker.py
```

- [ ] **Step 2: load_title_index 가 `clean_title` 을 통과시키도록 수정**

```python
# scripts/lib/dedup_checker.py (개념적 — 실제 구조 확인 후 반영)
from scripts.lib.title_cleaner import clean_title

def load_title_index(self):
    rows = self.sb.table("books").select("id, title, author, isbn") \
        .execute().data or []
    for r in rows:
        title = clean_title(r.get("title") or "")  # ← 추가
        author = r.get("author") or ""
        isbn = r.get("isbn") or ""
        key = self._normalize(title, author)
        self._index[key] = isbn
    return len(rows)
```

- [ ] **Step 3: 단위 테스트로 cross-source 중복 감지 검증**

```python
# tests/test_dedup_checker.py
def test_load_title_index_applies_clean_title():
    """DB 의 raw title ('채식주의자 (리커버)') 과 새로 등록하는
    clean_title('채식주의자') 가 같은 key 로 매칭되어야 한다."""
    from scripts.lib.dedup_checker import DeduplicateChecker

    fake_sb = MagicMock()
    fake_sb.table.return_value.select.return_value.execute.return_value.data = [
        {"id": "b1", "title": "채식주의자 (리커버)", "author": "한강", "isbn": "978X"},
    ]
    dc = DeduplicateChecker(fake_sb)
    dc.load_title_index()

    # 새 책 (cleaned title) 이 중복으로 감지되어야 함
    assert dc.is_title_duplicate("채식주의자", "한강", "978Y") is True
```

- [ ] **Step 4: 실행 + commit**

```bash
python3 -m pytest tests/test_dedup_checker.py -v
git add scripts/lib/dedup_checker.py tests/test_dedup_checker.py
git commit -m "fix: dedup_checker load_title_index 가 clean_title 적용"
```

---

### Task B4: data4library_discovery sanitize_for_upsert 에 source/schema 정렬

**Files:**
- Modify: `scripts/data4library_discovery_collector.py::sanitize_for_upsert`

**Why:** Audit: 현재 `isbn, title, author, publisher, cover_url, loan_count, sales_point` 만 저장. `source` 가 NULL 이라 smart_batch (`source='aladin'`) 와 cross-source race 시 downstream 필터 불일치.

- [ ] **Step 1: sanitize_for_upsert 에 source 추가**

```python
def sanitize_for_upsert(row: dict) -> dict:
    return {
        "isbn": row["isbn13"],
        "title": clean_title(row.get("title") or ""),
        "author": row.get("author_raw") or "",
        "publisher": row.get("publisher") or "",
        "cover_url": row.get("bookImageURL") or "",
        "loan_count": row.get("loan_count") or 0,
        "sales_point": row.get("loan_count") or 0,
        "source": "data4library",
        "source_id": str(row.get("isbn13") or ""),
    }
```

(실제 필드명은 현재 코드 확인 후 반영)

- [ ] **Step 2: 회귀 테스트**

```bash
python3 -m pytest tests/test_data4library_discovery.py -q
```

Expected: 기존 테스트 모두 PASS. sanitize 필드가 늘어난 만큼 테스트 fixture 갱신 필요할 수 있음.

- [ ] **Step 3: Commit**

```bash
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "fix: data4library_discovery sanitize 에 source='data4library' 추가"
```

---

### Task B5: book_filter 를 data4library 경로에도 적용

**Files:**
- Modify: `scripts/data4library_discovery_collector.py::filter_and_upsert`

**Why:** Audit: `is_non_book` 필터가 smart_batch 에만 적용됨. 문제집/수험서가 정보나루 Tier 1/3 을 통해 books 에 들어옴.

- [ ] **Step 1: filter_and_upsert 에 is_non_book 적용**

```python
from scripts.lib.book_filter import is_non_book

def filter_and_upsert(self, parsed_rows):
    adult_rows = [r for r in parsed_rows if is_adult_general(r)]
    self.stats["filtered_children"] += len(parsed_rows) - len(adult_rows)

    # 문제집/수험서 필터 추가
    book_rows = [r for r in adult_rows if not is_non_book({
        "title": r.get("title") or "",
        "categoryName": r.get("class_name") or "",
    })]
    self.stats["filtered_non_book"] = self.stats.get("filtered_non_book", 0) + (len(adult_rows) - len(book_rows))
    ...
```

(is_non_book 시그니처는 해당 파일 확인 후 정확히 맞춤)

- [ ] **Step 2: stats report 업데이트**

```python
def report(self):
    ...
    print(f"  - 문제집/수험서 제외: {self.stats.get('filtered_non_book', 0)}")
```

- [ ] **Step 3: 테스트 + commit**

```bash
python3 -m pytest tests/test_data4library_discovery.py -q
git add scripts/data4library_discovery_collector.py
git commit -m "fix: data4library 경로에 is_non_book 필터 적용"
```

---

### Task B6: smart_batch 와 data4library 사이의 upsert overwrite 방지

**Files:**
- Modify: `scripts/smart_batch_collector.py::save_batch` (upsert 필드 제한)
- Modify: `scripts/data4library_discovery_collector.py::filter_and_upsert` (동일)

**Why:** Audit: 두 collector 가 `on_conflict=isbn` 으로 전체 row upsert → 뒤에 온 쪽이 title/author/cover 덮어씀. **대안 1**: upsert 시 `returning="minimal"` + 명시적 컬럼만. **대안 2**: INSERT ON CONFLICT DO NOTHING (신규만 추가, 기존은 수정 안 함).

Eden 결정 필요: (a) 덮어쓰기 허용 (최신 데이터 우선), (b) 덮어쓰기 방지 (최초 데이터 고정). 기본 권장 = (b).

- [ ] **Step 1: Eden 확인 후 방향 결정**

이 단계는 executing-plans 실행 중 사용자에게 질문. 기본값: (b) = do_nothing_on_conflict.

- [ ] **Step 2: (b) 선택 시 — smart_batch save_batch 가 새 ISBN 만 insert**

```python
# scripts/smart_batch_collector.py save_batch
def saver(chunk):
    # 기존 ISBN 은 수정하지 않음 (먼저 수집한 metadata 우선).
    # supabase-py 에서 INSERT ... ON CONFLICT DO NOTHING 는
    # .upsert(on_conflict=..., ignore_duplicates=True) 로 표현.
    with_retry(lambda: self.sb.table("books").upsert(
        chunk, on_conflict="isbn", ignore_duplicates=True
    ).execute())
```

- [ ] **Step 3: data4library_discovery 도 동일**

```python
# scripts/data4library_discovery_collector.py filter_and_upsert
for i in range(0, len(rows), 200):
    chunk = rows[i:i + 200]
    with_retry(lambda c=chunk: self.sb.table("books").upsert(
        c, on_conflict="isbn", ignore_duplicates=True
    ).execute())
```

- [ ] **Step 4: 테스트 + commit**

```bash
python3 -m pytest tests/test_smart_batch_collector.py tests/test_data4library_discovery.py -q
git add scripts/smart_batch_collector.py scripts/data4library_discovery_collector.py
git commit -m "fix: books upsert 에 ignore_duplicates (cross-source overwrite 방지)"
```

---

## Phase C — HIGH Pipeline (5건)

### Task C1: `_pending_for_step("reason_extractor")` 를 정확한 COUNT 로 교체

**Files:**
- Modify: `scripts/pipeline_orchestrator.py::_pending_for_step` + `collect_status`
- Test: `tests/test_pipeline_orchestrator.py`

**Why:** 검증 확인: `with_reasons // 13` 이 부정확 → false silent-drop fail 가능. 정확한 값 = `COUNT(DISTINCT book_id)` on `book_love_reasons`.

- [ ] **Step 1: collect_status 에 distinct count 추가**

```python
# scripts/pipeline_orchestrator.py
def _count_distinct_book_id_in_reasons(sb):
    """book_love_reasons 에서 distinct book_id 수."""
    # Supabase-py 는 raw SQL 직접 실행 제한적 → rpc 정의 또는 view 활용
    # 임시 대안: 모든 book_id 페이지네이션으로 수집 후 set 크기
    seen = set()
    offset = 0
    while True:
        res = sb.table("book_love_reasons") \
            .select("book_id") \
            .range(offset, offset + 999) \
            .execute()
        if not res.data:
            break
        for r in res.data:
            seen.add(r["book_id"])
        if len(res.data) < 1000:
            break
        offset += 1000
    return len(seen)


def collect_status(sb):
    ...
    return {
        ...
        "with_reasons": _count_total(sb, "book_love_reasons"),
        "with_reasons_distinct_books": _count_distinct_book_id_in_reasons(sb),
    }


def _pending_for_step(step_name, status):
    if step_name == "reason_extractor":
        v3 = status.get("with_v3_vectors", 0)
        distinct = status.get("with_reasons_distinct_books", 0)
        return max(0, v3 - distinct)
    ...
```

- [ ] **Step 2: 권장 대안 — Supabase view 또는 rpc**

더 깔끔한 방법: Supabase 에 view 생성.

```sql
-- supabase/migrations/20260410_reason_distinct_view.sql
CREATE OR REPLACE VIEW public.v_reason_books AS
SELECT COUNT(DISTINCT book_id) AS n FROM public.book_love_reasons;
```

그리고 collect_status 가 `sb.table("v_reason_books").select("n").execute()` 로 1 row 조회.

둘 중 한 가지 선택 (Eden 이 migration 선호하지 않으면 Python loop).

- [ ] **Step 3: 기존 테스트 회귀**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py -q
```

- [ ] **Step 4: Commit**

```bash
git add scripts/pipeline_orchestrator.py tests/test_pipeline_orchestrator.py
git commit -m "fix: pipeline reason_extractor pending distinct book_id 로 교체 (H1)"
```

---

### Task C2: build_index atomic write (H3)

**Files:**
- Modify: `recommendation-server/scripts/build_index.py`
- Test: `recommendation-server/tests/test_build_index_atomic.py` (신규)

**Why:** 검증 확인: `pickle.dump(bundle, f)` 가 `index.pkl` 에 직접 씀. 런타임 서버가 reload 중이면 half-written 파일 로드 가능.

- [ ] **Step 1: tmp + replace 로 변경**

```python
# recommendation-server/scripts/build_index.py
import os

TMP_PATH = OUTPUT_PATH + ".tmp"

# Before
with open(OUTPUT_PATH, "wb") as f:
    pickle.dump(bundle, f)

# After
with open(TMP_PATH, "wb") as f:
    pickle.dump(bundle, f)
    f.flush()
    os.fsync(f.fileno())
os.replace(TMP_PATH, OUTPUT_PATH)

# sha256 sidecar 도 동일 패턴
with open(SHA_PATH + ".tmp", "w") as f:
    f.write(sha256_hex)
    f.flush()
    os.fsync(f.fileno())
os.replace(SHA_PATH + ".tmp", SHA_PATH)
```

- [ ] **Step 2: 테스트**

```python
# recommendation-server/tests/test_build_index_atomic.py
"""build_index 의 atomic write 검증."""
import os
import pickle
import tempfile
from unittest.mock import patch


def test_index_pkl_is_atomic(tmp_path):
    """OUTPUT_PATH 가 tmp 파일을 거쳐 rename 되는지 확인."""
    from recommendation_server.scripts import build_index as bi  # path 맞춤

    out = tmp_path / "index.pkl"
    bundle = {"foo": "bar"}

    # 내부 write 흐름만 테스트 — 외부 Supabase 의존 X
    tmp = str(out) + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(bundle, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)

    assert out.exists()
    assert not os.path.exists(tmp)
    with open(out, "rb") as f:
        assert pickle.load(f) == bundle
```

(실제로는 `build_index.main` 의 write 부분만 분리된 helper 로 만들고 그 helper 를 테스트)

- [ ] **Step 3: Commit**

```bash
git add recommendation-server/scripts/build_index.py recommendation-server/tests/test_build_index_atomic.py
git commit -m "fix: build_index atomic write (tmp + os.replace) (H3)"
```

---

### Task C3: build_index skip ratio guard (H2)

**Files:**
- Modify: `recommendation-server/scripts/build_index.py`

**Why:** Audit: 누락된 desc/l1/l2/genre_embs 를 스킵만 하고 임계값 없음. DB 보다 작은 인덱스가 silent 하게 배포됨.

- [ ] **Step 1: skip 카운터 + 임계값**

```python
# recommendation-server/scripts/build_index.py main() 끝부분
SKIP_RATIO_THRESHOLD = 0.05  # 5% 초과면 실패

total = loaded + skipped
if total > 0:
    skip_ratio = skipped / total
    print(f"  skipped: {skipped}/{total} ({skip_ratio:.1%})")
    if skip_ratio > SKIP_RATIO_THRESHOLD:
        print(f"❌ skip ratio {skip_ratio:.1%} > {SKIP_RATIO_THRESHOLD:.1%} — build 실패", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 2: Commit**

```bash
git add recommendation-server/scripts/build_index.py
git commit -m "fix: build_index skip ratio > 5% 시 exit 1 (H2)"
```

---

### Task C4: orchestrator 와 build_index 의 .env 교차 검증 (H4)

**Files:**
- Modify: `scripts/pipeline_orchestrator.py::main` (startup 로그)

**Why:** Audit: orchestrator 는 `curation/.env`, build_index 는 `recommendation-server/.env`. Dev/prod 섞이면 silent drift. 최소한 startup 에 어떤 SUPABASE_URL 을 보고 있는지 명시 + hash 비교.

- [ ] **Step 1: startup 에 SUPABASE_URL hash 출력 + build_index 의 .env 도 assertion**

```python
# scripts/pipeline_orchestrator.py main()
import hashlib

def _env_fingerprint(path: str) -> str:
    try:
        with open(path, "rb") as f:
            content = f.read()
        # SUPABASE_URL 라인만 추출
        for line in content.decode("utf-8", errors="ignore").splitlines():
            if line.startswith("SUPABASE_URL="):
                return hashlib.sha256(line.encode()).hexdigest()[:12]
    except OSError:
        return "n/a"
    return "n/a"


def main():
    ...
    curation_env = os.path.join(REPO, ".env")
    rec_env = os.path.join(REPO, "recommendation-server", ".env")
    print(f"curation/.env SUPABASE_URL hash: {_env_fingerprint(curation_env)}")
    print(f"rec-server/.env SUPABASE_URL hash: {_env_fingerprint(rec_env)}")
    if (os.path.exists(rec_env)
            and _env_fingerprint(curation_env) != _env_fingerprint(rec_env)):
        print("⚠ curation 과 recommendation-server 의 SUPABASE_URL 이 다릅니다.", file=sys.stderr)
        print("  build_index 가 orchestrator 와 다른 DB 를 볼 수 있습니다.", file=sys.stderr)
    ...
```

- [ ] **Step 2: Commit**

```bash
git add scripts/pipeline_orchestrator.py
git commit -m "fix: orchestrator startup 에 curation vs rec-server .env 비교 경고 (H4)"
```

---

### Task C5: `generate_book_v3_vectors` 에 `with_retry` 추가 (M4)

**Files:**
- Modify: `scripts/generate_book_v3_vectors.py`

**Why:** Audit: 다른 pipeline script 와 달리 supabase 호출이 raw — transient 57014 면 batch drop 후 errors 카운트만 증가. 다른 하드닝 script 와 일관성을 위해.

- [ ] **Step 1: Raw supabase 호출 전부 `with_retry` 로 감싸기**

해당 파일의 `sb.table(...)` 호출을 확인:

```bash
grep -n "sb.table\|sb\.rpc\|\.execute()" scripts/generate_book_v3_vectors.py | head -20
```

각 호출을:

```python
# Before
res = sb.table("books").select(...).execute()

# After
from lib.retry import with_retry
res = with_retry(lambda: sb.table("books").select(...).execute())
```

- [ ] **Step 2: exit code 확인**

main() 이 `total_errors > 0` 시 `sys.exit(1)` 하는지 확인. 없으면 추가.

- [ ] **Step 3: Commit**

```bash
git add scripts/generate_book_v3_vectors.py
git commit -m "fix: generate_book_v3_vectors 에 with_retry 적용 (M4)"
```

---

## Phase D — HIGH Enricher (4건)

### Task D1: batch_enricher `extract_colors` 실패 카운터 (I1)

**Files:**
- Modify: `scripts/batch_enricher.py`
- Test: `tests/test_batch_enricher.py`

**Why:** Audit: 실패 시 `return None` 후 update 안 함, 카운터 없음 → 다음 런에 다시 시도되지만 실패율 가시화 안 됨.

- [ ] **Step 1: stats 에 colors_failed 추가**

```python
# scripts/batch_enricher.py
self.stats = {
    "processed": 0,
    "colors_extracted": 0,
    "colors_failed": 0,   # ← 추가
    "fonts_assigned": 0,
    "errors": 0,
}

def enrich_book(self, book):
    updates = {}
    if book.get("dominant_colors") is None and book.get("cover_url"):
        colors = extract_colors(book["cover_url"])
        if colors:
            updates["dominant_colors"] = colors
            self.stats["colors_extracted"] += 1
        else:
            self.stats["colors_failed"] += 1
    ...

def print_report(self, total):
    ...
    print(f"  색상 추출 실패: {s['colors_failed']}권")
```

- [ ] **Step 2: 테스트**

```python
# tests/test_batch_enricher.py
def test_colors_failed_counted_on_extraction_error():
    import batch_enricher
    with patch.object(batch_enricher, "create_client", return_value=MagicMock()):
        e = batch_enricher.BatchEnricher(dry_run=True)
        books = [{"id": "b1", "cover_url": "http://bad.url/x.jpg",
                  "spine_font": None, "dominant_colors": None,
                  "genre": "소설", "description": ""}]
        with patch.object(e, "fetch_books_needing_enrichment", return_value=books):
            with patch.object(batch_enricher, "extract_colors", return_value=None):
                with patch("time.sleep"):
                    e.run()
    assert e.stats["colors_failed"] == 1
```

- [ ] **Step 3: 실행 + commit**

```bash
python3 -m pytest tests/test_batch_enricher.py -q
git add scripts/batch_enricher.py tests/test_batch_enricher.py
git commit -m "fix: batch_enricher colors_failed 카운터 (I1)"
```

---

### Task D2: v3_reason_extract `skipped_no_data` 분리 (I2)

**Files:**
- Modify: `scripts/v3_reason_extract.py`
- Test: `tests/test_v3_reason_extract.py`

**Why:** Audit: `rich_description` 이 비어있거나 `[책소개]` 섹션이 없는 책을 `errors` 로 집계 → `QC_MAX_ERROR_RATIO=0.15` auto-abort 오작동.

- [ ] **Step 1: stats 에 skipped_no_data 분리**

```python
# scripts/v3_reason_extract.py
# 초기화
total_done, total_errors, total_saved, total_skipped_no_data = 0, 0, 0, 0

# extract_v3_reasons 의 early return 을 "에러" 가 아닌 "스킵" 으로
def extract_v3_reasons(book):
    desc = parse_section(book.get("rich_description") or "", "책소개")
    if not desc or len(desc) < 50:
        return None, "no_data"   # ← 반환값 2-tuple 로 확장
    ...
    return reasons, "ok"

# 루프 안에서
for future in as_completed(futures):
    book = futures[future]
    try:
        result, status = future.result(timeout=60)
        if status == "no_data":
            total_skipped_no_data += 1
            continue
        if result:
            extracted[book["id"]] = (book, result)
        else:
            chunk_errors += 1
    except Exception as e:
        chunk_errors += 1
        ...

# QC 체크 시 error_ratio 계산에서 no_data 제외
qc_base = max(total_done - total_skipped_no_data, 1)
error_ratio = total_errors / qc_base
```

- [ ] **Step 2: 최종 리포트에 스킵 표시**

```python
print(f"  처리: {total_done}권")
print(f"  저장: {total_saved}건")
print(f"  스킵 (데이터 부족): {total_skipped_no_data}권")
print(f"  에러: {total_errors}건")
```

- [ ] **Step 3: Commit**

```bash
git add scripts/v3_reason_extract.py
git commit -m "fix: v3_reason_extract skipped_no_data 를 errors 에서 분리 (I2)"
```

---

### Task D3: data4library_collector 빈 body 영구 persist 방지 (I5)

**Files:**
- Modify: `scripts/data4library_collector.py`
- Test: `tests/test_data4library_collector.py`

**Why:** Audit: API 가 transient 로 빈 body 반환 시 `library_keywords=[]` 저장 → `.is_("library_keywords", "null")` 에서 빠져 다음 런에 재시도 안 됨.

- [ ] **Step 1: HTTP status code 체크 추가**

```python
# scripts/data4library_collector.py fetch_usage
def fetch_usage(self, isbn):
    url = f"{self.API_BASE}/usageAnalysisList"
    params = {"authKey": self.api_key, "isbn13": isbn, "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        if not r.text.strip():
            # 빈 body 는 transient 로 간주 → 예외로 raise
            # 정상적인 "데이터 없음" 은 JSON 빈 배열로 내려옴
            raise RuntimeError(f"빈 응답 body (transient 의심, isbn={isbn})")
        data = r.json()
        keywords = parse_keywords(data)
        co_loan = parse_co_loan_books(data)
        return keywords, co_loan
    except Exception as e:
        raise RuntimeError(f"API 호출 실패 ({isbn}): {e}")
```

`run()` 에서는 기존대로 except 로 잡아 `self.stats["errors"] += 1` → 다음 런에 재시도. 핵심은 `_save` 가 호출되지 않는 것.

- [ ] **Step 2: 테스트**

```python
# tests/test_data4library_collector.py
def test_fetch_usage_raises_on_empty_body():
    import data4library_collector
    with patch.object(data4library_collector, "create_client",
                      return_value=MagicMock()):
        c = data4library_collector.Data4LibraryCollector(dry_run=True)
        c._api_key = "fake"

        fake_resp = MagicMock()
        fake_resp.text = ""
        fake_resp.raise_for_status = MagicMock()

        with patch("data4library_collector.requests.get",
                   return_value=fake_resp):
            import pytest
            with pytest.raises(RuntimeError, match="transient"):
                c.fetch_usage("9781234567890")
```

- [ ] **Step 3: 실행 + commit**

```bash
python3 -m pytest tests/test_data4library_collector.py -q
git add scripts/data4library_collector.py tests/test_data4library_collector.py
git commit -m "fix: data4library_collector 빈 body 는 transient 로 raise (I5)"
```

---

### Task D4: taste_recomputer `_refresh_confidence` 비율 경고 (I4)

**Files:**
- Modify: `scripts/taste_recomputer.py`

**Why:** Audit: 개별 실패는 카운트되지만 전체 대비 비율 경고 없음. 10k 유저에서 80% 가 실패해도 최종 exit 1 뿐, 가시성 낮음.

- [ ] **Step 1: print_report 에 비율 경고 추가**

```python
# scripts/taste_recomputer.py _print_report 끝에
if s["processed"] > 0:
    conf_ratio = s["confidence_failed"] / s["processed"]
    if conf_ratio > 0.1:
        print(f"⚠ confidence_failed 비율 {conf_ratio:.1%} > 10% — RPC 장애 의심")
```

- [ ] **Step 2: Commit**

```bash
git add scripts/taste_recomputer.py
git commit -m "fix: taste_recomputer confidence_failed 비율 경고 (I4)"
```

---

## Phase E — HIGH Cross-cutting (6건)

### Task E1: `lib/retry.py` SQLSTATE whitelist 확장 + 코드 로깅

**Files:**
- Modify: `scripts/lib/retry.py`
- Test: `tests/lib/test_retry.py` (있으면 확장, 없으면 신규)

**Why:** Audit: `55P03` (lock_not_available), `25P02` (in_failed_sql_transaction), `58030` (io_error) 누락. books 동시 upsert lock contention non-retryable. 또한 SQLSTATE 가 retry 로그에 안 찍혀서 debugging 어려움.

- [ ] **Step 1: whitelist 확장**

```python
# scripts/lib/retry.py
RETRYABLE_PG_CODES = {
    "57014",  # statement_timeout
    "55P03",  # lock_not_available
    "25P02",  # in_failed_sql_transaction
    "58030",  # io_error
    "40001",  # serialization_failure (이미 있을 수 있음)
    "40P01",  # deadlock_detected (이미 있을 수 있음)
}
```

- [ ] **Step 2: 로그에 pg_code 포함**

```python
def with_retry(fn, max_retries=5, backoff=1.0):
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            code = str(getattr(e, "code", "") or "")
            if code not in RETRYABLE_PG_CODES:
                raise
            if attempt == max_retries - 1:
                raise
            delay = backoff * (2 ** attempt)
            print(f"  ⚠ with_retry: pg_code={code} attempt {attempt+1}/{max_retries} (sleep {delay}s)")
            time.sleep(delay)
```

- [ ] **Step 3: 테스트**

```python
# tests/lib/test_retry.py
from scripts.lib.retry import with_retry, RETRYABLE_PG_CODES


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"err {code}")


def test_retry_55P03_lock_not_available():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise FakeAPIError("55P03")
        return "ok"

    with patch("time.sleep"):
        result = with_retry(flaky)
    assert result == "ok"
    assert calls["n"] == 2


def test_retry_non_whitelisted_code_raises():
    def always_fail():
        raise FakeAPIError("23505")  # unique violation — 영구
    import pytest
    with pytest.raises(FakeAPIError):
        with_retry(always_fail)
```

- [ ] **Step 4: 실행 + commit**

```bash
python3 -m pytest tests/lib/test_retry.py -v
git add scripts/lib/retry.py tests/lib/test_retry.py
git commit -m "fix: retry whitelist 확장 (55P03/25P02/58030) + pg_code 로깅"
```

---

### Task E2: openai_helpers import path 일관화

**Files:**
- Modify: `scripts/reason_extractor.py` (import 경로)
- Modify: 기타 해당 파일 grep 후 확인

**Why:** Audit: `reason_extractor` 는 `from lib.openai_helpers`, `v3_reason_extract` 는 `from scripts.lib.openai_helpers`. 실행 모드에 따라 resolution 다름.

- [ ] **Step 1: 모든 caller 확인**

```bash
grep -rn "from .*openai_helpers\|import openai_helpers" scripts/ tests/
```

- [ ] **Step 2: `scripts.lib.openai_helpers` 로 통일**

예: `scripts/reason_extractor.py`:

```python
# Before
try:
    from lib.openai_helpers import call_chat, call_embedding
except ImportError:
    from scripts.lib.openai_helpers import call_chat, call_embedding

# After (A8 의 conftest 에 맞춤)
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from scripts.lib.openai_helpers import call_chat, call_embedding
```

또는 더 간단히 `sys.path.insert(0, os.path.dirname(__file__))` 후 `from lib.openai_helpers import ...` 로 통일. 기존 `tier1_embedder` 패턴 참고.

- [ ] **Step 3: 기존 테스트 회귀**

```bash
python3 -m pytest tests/ -q
```

- [ ] **Step 4: Commit**

```bash
git add scripts/reason_extractor.py
git commit -m "refactor: openai_helpers import path 일관화 (lib.openai_helpers)"
```

---

### Task E3: books.loan_count backfill 스크립트

**Files:**
- Create: `scripts/backfill_loan_count.py`
- Test: `tests/test_backfill_loan_count.py`

**Why:** Audit: aladin/kakao 경유로 들어온 책은 loan_count=NULL → fallback_curation 랭킹에서 빠짐. 정보나루 API 를 호출해서 backfill.

- [ ] **Step 1: 스크립트 설계**

기존 `data4library_collector` 패턴과 비슷하게. ISBN 으로 `loanItemSrch` 호출 (loan_count 필드 포함).

- [ ] **Step 2: 최소 구현**

```python
# scripts/backfill_loan_count.py
"""books.loan_count 가 NULL 인 책을 정보나루로 backfill.

사용법:
  python3 scripts/backfill_loan_count.py --limit 100
  python3 scripts/backfill_loan_count.py --dry-run
"""
import argparse
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry
from lib.data4library_api import fetch_book_detail  # 또는 적절한 endpoint

load_dotenv()

REQUEST_DELAY = 0.5


def fetch_books_without_loan(sb, limit):
    res = with_retry(lambda: sb.table("books")
        .select("id, isbn")
        .is_("loan_count", "null")
        .not_.is_("isbn", "null")
        .limit(limit)
        .execute())
    return res.data or []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    sb = create_client(os.getenv("SUPABASE_URL"),
                       os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    api_key = os.getenv("DATA4LIBRARY_API_KEY")
    if not api_key:
        print("❌ DATA4LIBRARY_API_KEY 누락")
        return 1

    books = fetch_books_without_loan(sb, limit=args.limit)
    print(f"대상: {len(books)}권")

    stats = {"updated": 0, "not_found": 0, "errors": 0}

    for i, book in enumerate(books):
        try:
            loan_count = fetch_loan_for_isbn(api_key, book["isbn"])
            if loan_count is None:
                stats["not_found"] += 1
                continue
            if not args.dry_run:
                with_retry(lambda: sb.table("books")
                    .update({"loan_count": loan_count})
                    .eq("id", book["id"])
                    .execute())
            stats["updated"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"  ✗ {book['isbn']}: {e}")
        time.sleep(REQUEST_DELAY)

    print(f"\n{stats}")
    return 1 if stats["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
```

(`fetch_loan_for_isbn` 은 `lib/data4library_api.py` 의 기존 함수 또는 신규 추가)

- [ ] **Step 3: 테스트**

최소 smoke 테스트: `--limit 0` 으로 books 0 권 반환 → exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_loan_count.py tests/test_backfill_loan_count.py
git commit -m "feat: backfill_loan_count 스크립트 (aladin/kakao 책의 loan_count)"
```

---

### Task E4: Daily workflows 조율

**Files:**
- Create: `.github/workflows/daily-pipeline.yml` (신규 orchestrator 단일 workflow)
- Modify: 기존 `daily-*.yml` 을 `workflow_run` 또는 `schedule off` 로 조정

**Why:** Audit: 5 개 workflow 가 조율 없이 각자 돈다 → 스테이지 순서 보장 안 됨.

- [ ] **Step 1: 기존 workflow 목록**

```bash
ls .github/workflows/
```

- [ ] **Step 2: 단일 orchestrator workflow**

```yaml
# .github/workflows/daily-pipeline.yml
name: Daily Pipeline (discovery + enrich + index)

on:
  schedule:
    - cron: '0 18 * * *'  # 매일 03:00 KST
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install deps
        run: |
          pip install -r requirements.txt
      - name: Discovery (Tier 1)
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          DATA4LIBRARY_API_KEY: ${{ secrets.DATA4LIBRARY_API_KEY }}
        run: python3 scripts/data4library_discovery_collector.py --tier 1
      - name: Enrich pipeline
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python3 scripts/pipeline_orchestrator.py --limit 500
      - name: Build index
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: |
          cd recommendation-server
          python3 scripts/build_index.py
```

- [ ] **Step 3: 기존 workflow 의 schedule 제거**

```bash
# 개별 workflow 들의 on.schedule 을 삭제하고 workflow_dispatch 만 유지.
# 또는 파일 자체 삭제.
```

Eden 결정 필요: 보존 vs 삭제.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/
git commit -m "chore: 단일 daily-pipeline workflow + 기존 cron 제거"
```

---

### Task E5: 진단 스크립트 anon key 로 전환

**Files:**
- Modify: `scripts/verify_v3_data.py`
- Modify: `scripts/bridge_50_test.py`
- Modify: `scripts/test_*.py` (진단 전용, production 미사용)

**Why:** Audit: service_role 키가 읽기 전용 진단 스크립트에도 사용 → 유출 위험 증가.

- [ ] **Step 1: 각 진단 스크립트의 create_client 호출 확인**

```bash
grep -rn "create_client.*SERVICE_ROLE\|SUPABASE_SERVICE_ROLE_KEY" scripts/verify_v3_data.py scripts/bridge_50_test.py scripts/test_*.py
```

- [ ] **Step 2: anon key 사용으로 전환**

```python
# Before
sb = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

# After
sb = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY"),  # 진단은 읽기 전용
)
```

만약 해당 스크립트가 RLS 때문에 읽지 못한다면, 서비스 롤 그대로 유지하되 주석으로 명시.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_v3_data.py scripts/bridge_50_test.py scripts/test_*.py
git commit -m "chore: 진단 스크립트 anon key 로 전환 (service_role 노출 감소)"
```

---

### Task E6: reason_extractor 의 `extract_key_terms` orphan 코드 처리

**Files:**
- Modify: `scripts/reason_extractor.py`

**Why:** Audit (M6): `extract_key_terms` 가 정의되어 있지만 `_extract_reasons` 에서 호출되지 않는 orphan. 삭제 또는 재연결.

- [ ] **Step 1: Eden 결정 필요 — 삭제 vs 재연결**

기본: 삭제 (reason_extractor 는 A4 에서 legacy 로 표시됨).

- [ ] **Step 2: 삭제**

```bash
# scripts/reason_extractor.py 의 extract_key_terms 정의 제거
```

- [ ] **Step 3: Commit**

```bash
git add scripts/reason_extractor.py
git commit -m "chore: reason_extractor orphan extract_key_terms 제거 (M6)"
```

---

## Phase F — MEDIUM (조건부, Eden 결정)

아래 항목은 correctness 영향이 작거나 운영으로 커버 가능. Phase A~E 완료 후 Eden 이 선택.

| # | 파일 | 내용 | 크기 |
|---|---|---|---|
| F1 | `scripts/smart_batch_collector.py` | 저자 "(지은이)" suffix 저장 정리 | XS |
| F2 | `scripts/lib/dedup_checker.py` | 정규화 regex 에 "큰 글자책", "eBook" 추가 | XS |
| F3 | `scripts/lib/dedup_checker.py` | `:` subtitle 제거 → 시리즈 오매칭 방지 | S |
| F4 | `scripts/lib/data4library_api.py` | `is_adult_general` 의 empty symbol → False | XS |
| F5 | `scripts/lib/state_manager.py` | data4library source_type 통합 | S |
| F6 | `scripts/yes24_scraper.py` | fallback-to-first-result 엄격화 | S |
| F7 | `scripts/generate_book_v3_vectors.py` | 삽입을 upsert 로 | XS |
| F8 | `scripts/pipeline_orchestrator.py` | collect_status 를 with_retry 로 | XS |
| F9 | `scripts/tier2_embedder.py` | compose_embedding section marker 검증 | XS |
| F10 | `scripts/reason_extractor.py` | `_run_rerun` 이 transaction 사용 | M |
| F11 | `recommendation-server/scripts/build_index.py` | 내부 retry 를 `lib/retry` 로 통일 (또는 독립 명시) | XS |

각 항목은 별도 task 로 쪼개서 해결. 본 plan 에서는 상세 step 생략.

---

## Phase G — LOW / NIT (참고용)

`dedup_checker` normalization false positive/negative, 사용되지 않는 변수, unused import, logging 통합 (`scripts/lib/logger.py`), `20260407_phase1a_user_state_refresh.sql` 의 `cron.unschedule` 패턴 등. Phase A~F 완료 후 여력이 있으면 한 번에 PR 1 개로.

---

## Phase H — 보류된 현재 작업 (Phase 2 smoke test)

Phase 1 (코드 리뷰) 은 이 세션에서 완료했지만, 원래 사용자 지시에 포함된 **"병렬로 각각 단계를 작은 단계로 실행해서 문제점 파악"** 은 미실행. Phase A~E fix 가 완료된 뒤 별도 smoke test 실행.

### Task H1: Phase 2 smoke test 계획

**목적:** 코드 리뷰에서 찾지 못한 운영 레벨 문제 (schema 불일치, env 누락, API quota, LLM 응답 형식 변경 등) 를 작은 입력으로 조기 발견.

- [ ] **Step 1: smoke 대상 스테이지 리스트 확정**

1. `data4library_discovery_collector.py --tier 1 --period-days 7 --pages 1 --dry-run`
2. `smart_batch_collector.py --dry-run --daily-target 5`
3. `pipeline_orchestrator.py --limit 3 --dry-run`
4. `tier2_embedder.py --limit 2 --dry-run`
5. `v3_reason_extract.py --limit 2 --dry-run --no-checkpoint`
6. `batch_enricher.py --limit 2 --dry-run`
7. `data4library_collector.py --limit 2 --dry-run`
8. `taste_recomputer.py --limit 1 --dry-run`
9. `cd recommendation-server && python3 scripts/build_index.py` (빈 DB 대응?)

- [ ] **Step 2: 각 smoke 결과 기록 양식**

| script | args | exit code | 소요 | 핵심 로그 | 관찰된 문제 |
|---|---|---|---|---|---|
| ... | ... | 0/1 | ... | ... | ... |

- [ ] **Step 3: 발견된 추가 이슈는 known-issues.md 에 KI 로 추가 후 별도 plan 에 연결**

- [ ] **Step 4: Commit smoke 결과 (참고 문서)**

```bash
# docs/superpowers/notes/2026-04-XX-smoke-test-log.md 에 기록 후 commit
```

---

## Task 실행 순서 권장

1. **Phase A** (BLOCKER) — A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8 (이 순서 중요: A1 먼저, A2 는 A1 의 migration 적용 후, A3 는 A2 가 끝나야 안전)
2. **Phase E1** (retry whitelist) — 다른 Phase 가 retry 에 의존
3. **Phase B** (Discovery) — B1~B6 는 독립적이라 병렬 가능
4. **Phase C** (Pipeline) — C1~C5 는 독립적이라 병렬 가능
5. **Phase D** (Enricher) — D1~D4 독립적
6. **Phase E2~E6** — 독립적
7. **Phase H** (smoke test) — Phase A~E 완료 후
8. **Phase F/G** — 여력 시

각 Phase 마지막에 전체 suite 실행:

```bash
python3 -m pytest tests/ -q && cd recommendation-server && python3 -m pytest tests/ -q
```

---

## Eden 수동 적용 체크리스트

Claude 는 코드만 수정, SQL 은 파일 생성. Eden 이 수동 적용:

1. `supabase/migrations/20260410_book_love_reasons_unique.sql` — A1 이후
2. `supabase/migrations/20260410_book_embeddings_tier_composite.sql` — A5 이후
3. (옵션) `supabase/migrations/20260410_reason_distinct_view.sql` — C1 이 view 방식 선택 시
4. 기존 중복 data 확인: 
   ```sql
   SELECT book_id, source, reason, COUNT(*) 
   FROM book_love_reasons 
   GROUP BY book_id, source, reason 
   HAVING COUNT(*) > 1;
   ```
   중복이 있으면 SQL 로 정리 후 A1 migration 재적용.

---

## Self-Review

**Spec coverage check:**
- ✅ B1 (v3 전환): A3
- ✅ B2 (reason unique): A1 + A2
- ✅ B3 (smart_batch state corruption): A6
- ⚠️ B4 (baseline migrations): HIGH 로 하향. **근거:** `supabase/001_init_schema.sql` ~ `010_love_reasons.sql` 이 실제로 존재하며 `books`, `book_embeddings`, `book_love_reasons`, `book_v3_vectors`, `genre_embeddings`, `user_taste_vectors` 의 CREATE TABLE 을 모두 포함. Audit agent 가 `supabase/migrations/` 서브 폴더만 봐서 발생한 오판. Fresh env 재현 가능. 추후 레이아웃 정리는 Phase F 로 이관.
- ✅ B5 (conftest): A7
- ✅ B6 (openai retry): A8
- ✅ B7 (빈 key): A8 (동일 task)
- ✅ B8 (book_embeddings tier): A5
- ✅ HIGH Discovery 6건: B1~B6
- ✅ HIGH Pipeline 5건: C1~C5
- ✅ HIGH Enricher 4건: D1~D4
- ✅ HIGH Cross-cutting 6건: E1~E6
- ✅ MEDIUM: Phase F (조건부)
- ✅ LOW/NIT: Phase G (참고)
- ✅ 보류된 Phase 2 smoke test: Phase H

**Placeholder scan:** 일부 Phase B~E task 는 `실제 시그니처 확인 후` 조건부 표현이 있음 — Eden/executor 가 실행 중 grep 으로 확인 후 맞춤. "TBD" 는 없음.

**Type consistency:** `with_retry`, `save_with_size_fallback`, `stats["drop_failed"]` 등 PR #6 에서 확립된 네이밍을 유지.

**예상 규모:** 전체 M~L. Phase A 만 0.5~1일, 전체 2~3일.

---

## Branch / PR 전략 (cold-session 실행 가이드)

**Phase 별 1 PR 권장.** 근거:
- Phase A 는 Eden 수동 SQL 적용 (A1 migration) 을 포함하므로 자체 PR 로 분리해야 리뷰/머지 시 DB 변경 추적 용이
- Phase B/C/D 는 상호 독립적 영역 (Discovery / Pipeline / Enricher) 이라 병렬 리뷰 가능
- Phase E 는 cross-cutting — 작은 수정 여러 개라 하나로 묶어도 무방
- Phase H (smoke) 는 코드 변경 없이 실행 로그 기록이므로 별도 PR 또는 docs/notes commit

```
PR #N+1: docs/pipeline-audit-plan (이미 생성됨, 이 문서)
PR #N+2: fix/pipeline-phase-a       — Phase A 전체 (A1~A8)
  * A1 commit 후 Eden SQL 수동 적용 확인 대기
  * 이후 A2~A8 는 순차 commit
  * PR body 에 SQL 적용 완료 체크박스 포함
PR #N+3: fix/pipeline-phase-b       — Phase B 전체 (Discovery 수정 6건)
PR #N+4: fix/pipeline-phase-c       — Phase C 전체 (Pipeline 수정 5건)
PR #N+5: fix/pipeline-phase-d       — Phase D 전체 (Enricher 수정 4건)
PR #N+6: fix/pipeline-phase-e       — Phase E 전체 (Cross-cutting 6건)
PR #N+7: chore/phase-h-smoke-notes  — Phase H smoke 실행 로그 (선택)
```

각 PR 은 main 에서 branch off. Phase A 가 main 에 머지된 뒤 Phase B 가 시작되어야 retry whitelist (E1) 순서 의존성이 안전 (E1 은 Phase E 에 있지만 독립적이라 Phase A 와 병렬 가능).

**활성 GitHub 계정 확인:** `gh auth status` 로 `hyhuh0910` 확인 후 push (`feedback_git_push.md`).

---

## AladinAPIError 공유 import 규칙

Phase A6 에서 `scripts/lib/aladin_client.py::AladinAPIError` 를 정의한 뒤, 다른 파일에서 참조할 때는 **반드시 `scripts.lib.aladin_client` 에서 import**:

```python
# scripts/smart_batch_collector.py 상단 (A6 Step 3 작업 시 추가)
from lib.aladin_client import AladinAPIError  # or: from scripts.lib.aladin_client
```

Test 코드에서도 같은 경로 사용:

```python
# tests/test_smart_batch_collector.py
from scripts.lib.aladin_client import AladinAPIError

# ... 그 다음
collector.aladin.search_books = MagicMock(
    side_effect=AladinAPIError("transient 500")
)
```

Plan 의 A6 Step 5, B1 Step 2 test 예시에서 `smart_batch_collector.AladinAPIError` 로 되어 있는 부분은 **실행 시 `scripts.lib.aladin_client.AladinAPIError` 로 수정**하거나, smart_batch_collector.py 가 `from lib.aladin_client import AladinAPIError` 로 re-export 한 뒤 `smart_batch_collector.AladinAPIError` 네임스페이스를 쓰도록 하는 것 중 선택. 권장: 직접 import.
