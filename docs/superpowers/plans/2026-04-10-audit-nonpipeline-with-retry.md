# Audit & Harden Non-Pipeline with_retry Callers (KI-002) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 파이프라인 orchestrator 가 호출하지 않지만 Eden 이 수동 운영하는 6개 enrich 스크립트의 silent-failure 위험 제거. 특히 vector 컬럼에 쓰는 3개 (taste_recomputer, tier2_embedder, smart_batch_collector) 는 statement_timeout 에 노출됨.

**Architecture:** 파이프라인 하드닝 PR (#3, #4) 에서 확립된 패턴을 나머지 스크립트에 적용. 패턴:
1. `lib.retry` hard import (silent ImportError fallback 제거)
2. batch 실패 카운터 + exit code 전파
3. vector column upsert 는 배치 사이즈 fallback 추가 (50→20→5→1)
4. 단위 테스트: exit code 계약 + fallback 경로

**Tech Stack:** Python 3 + pytest + monkeypatch + unittest.mock

**참고:**
- `docs/superpowers/known-issues.md` KI-002
- **패턴 참고:** `scripts/tier1_embedder.py` (fa24f15) 가 표준 템플릿
- `scripts/lib/retry.py` 는 이미 PG SQLSTATE whitelist 포함 → Postgres 57014 는 자동 retry

**검증된 사실 (audit, 2026-04-09):**

| 파일 | 심각도 | DB 쓰기 패턴 | 치명 약점 |
|------|--------|-------------|----------|
| `scripts/taste_recomputer.py` | 🔴 Critical | `user_taste_vectors` multi-row insert, vector 컬럼 | chunking 없음, fallback 없음, `_refresh_confidence` 가 except 안에서 silent pass |
| `scripts/tier2_embedder.py` | 🔴 Critical | `book_embeddings` upsert, vector 컬럼, BATCH=50 | mini-batch fallback 없음, batch 실패 시 errors++ 만 하고 계속 |
| `scripts/smart_batch_collector.py` | 🔴 Critical | `books` upsert, BATCH=50 | per-row fallback 이 `except Exception: pass` 로 silent drop |
| `scripts/batch_enricher.py` | 🟡 Important | `books` per-row update | silent ImportError fallback, 카운터는 있지만 exit code 없음 |
| `scripts/data4library_collector.py` | 🟡 Important | `books` per-row update (library_keywords) | silent ImportError fallback, exit code 없음 |
| `scripts/v3_reason_extract.py` | 🟡 Important | `book_love_reasons` insert, vector | 이미 robust (total_errors, per-row fallback, consecutive-error auto-stop). **exit code만 없음.** |

---

## File Structure

**Modify (6개 스크립트 + 각 단위 테스트):**
- `scripts/taste_recomputer.py` + `tests/test_taste_recomputer.py` (신규 or 기존 확장)
- `scripts/tier2_embedder.py` + `tests/test_tier2_embedder.py`
- `scripts/smart_batch_collector.py` + `tests/test_smart_batch_collector.py`
- `scripts/batch_enricher.py` + `tests/test_batch_enricher.py`
- `scripts/data4library_collector.py` + `tests/test_data4library_collector.py`
- `scripts/v3_reason_extract.py` + `tests/test_v3_reason_extract.py` (이미 있으면 exit code 테스트만 추가)

**Create (공통 helper — DRY):**
- `scripts/lib/batch_fallback.py` — `save_with_size_fallback` 재사용 가능 함수 (tier1_embedder 에서 리팩터로 뽑아낼지 결정)
- `tests/lib/test_batch_fallback.py`

**Do NOT modify:**
- `scripts/lib/retry.py` (이미 하드닝됨)
- `scripts/tier1_embedder.py` / `scripts/reason_extractor.py` / `scripts/yes24_scraper.py` (이미 하드닝됨)

---

## 전체 접근 순서

**Phase A — 공통 helper 추출 (Critical fix 들이 공유)**
- Task 1: `batch_fallback.save_with_size_fallback` 을 `lib/batch_fallback.py` 로 추출 (from tier1_embedder)
- Task 2: tier1_embedder 가 helper 사용하도록 리팩터 (기존 test 유지)

**Phase B — Critical 3개 (vector 컬럼 위험)**
- Task 3: `tier2_embedder.py` — helper 적용 + exit code + tests
- Task 4: `taste_recomputer.py` — 자체 chunking 로직 + exit code + tests
- Task 5: `smart_batch_collector.py` — silent per-row pass 제거 + 카운터 + exit code + tests

**Phase C — Important 3개 (text-only 또는 자체 retry)**
- Task 6: `v3_reason_extract.py` — exit code 만 추가 (가장 작음)
- Task 7: `batch_enricher.py` — hard import + exit code + tests
- Task 8: `data4library_collector.py` — hard import + exit code + tests

**Phase D — 정리**
- Task 9: `known-issues.md` KI-002 제거
- Task 10: PR 생성

---

## Task 1: batch_fallback helper 추출

**Files:**
- Create: `scripts/lib/batch_fallback.py`
- Create: `tests/lib/test_batch_fallback.py`

**Why:** 동일한 50→20→5→1 fallback 로직을 4개 스크립트 (tier1, tier2, taste, smart_batch) 가 공유. DRY.

- [ ] **Step 1: Failing tests**

`tests/lib/test_batch_fallback.py`:

```python
"""save_with_size_fallback 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from unittest.mock import MagicMock
import pytest

from scripts.lib.batch_fallback import save_with_size_fallback


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"err {code}")


def test_first_try_success():
    saver = MagicMock()
    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5],
        is_timeout=lambda e: False,
    )
    assert saved == 50
    assert failed == 0
    assert saver.call_count == 1


def test_timeout_falls_back():
    call_count = {"n": 0}
    def saver(chunk):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise FakeAPIError("57014")
        # ok

    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5],
        is_timeout=lambda e: str(getattr(e, "code", "")) == "57014",
    )
    assert saved == 50
    assert failed == 0
    # 1 (50) + 3 (20+20+10) = 4
    assert call_count["n"] == 4


def test_permanent_error_drops_chunk():
    def saver(chunk):
        raise FakeAPIError("23505")  # unique_violation
    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5],
        is_timeout=lambda e: str(getattr(e, "code", "")) == "57014",
    )
    assert saved == 0
    assert failed == 50


def test_persistent_timeout_gives_up_at_singles():
    def saver(chunk):
        raise FakeAPIError("57014")
    items = list(range(5))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5],
        is_timeout=lambda e: str(getattr(e, "code", "")) == "57014",
    )
    assert saved == 0
    assert failed == 5
```

- [ ] **Step 2: Verify failing**

```bash
python3 -m pytest tests/lib/test_batch_fallback.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement helper**

`scripts/lib/batch_fallback.py`:

```python
"""배치 사이즈 fallback helper.

일시적 DB 부하로 큰 배치가 timeout 나면 자동으로 작은 단위로 쪼개서 재시도.
한 row 만 쪼개도 timeout 나면 포기하고 실패 카운트 증가.

이 helper 는 순수 python — DB 연결이나 pgcode 에 직접 의존하지 않는다.
호출자가 `saver(chunk)` 함수와 `is_timeout(exc)` 판별기를 주입한다.
"""
from __future__ import annotations

from typing import Callable, List, Tuple, TypeVar

T = TypeVar("T")


def save_with_size_fallback(
    items: List[T],
    saver: Callable[[List[T]], None],
    fallback_sizes: List[int],
    is_timeout: Callable[[Exception], bool],
) -> Tuple[int, int]:
    """items 를 saver 로 저장. timeout 이면 fallback_sizes 순서로 쪼갠다.

    Returns:
        (saved_count, failed_count) 합은 len(items).
    """
    total = len(items)
    if total == 0:
        return 0, 0

    # 첫 시도 (전체)
    try:
        saver(items)
        return total, 0
    except Exception as e:
        if not is_timeout(e):
            return 0, total

    def _next_smaller(current: int):
        for fb in fallback_sizes:
            if fb < current:
                return fb
        return None

    saved = 0
    failed = 0
    initial = _next_smaller(total)
    queue: List[List[T]] = []
    if initial is None:
        if total > 1:
            queue = [items[j:j+1] for j in range(total)]
        else:
            return 0, 1
    else:
        queue = [items[j:j+initial] for j in range(0, total, initial)]

    while queue:
        cur = queue.pop(0)
        size = len(cur)
        try:
            saver(cur)
            saved += size
            continue
        except Exception as e:
            if not is_timeout(e):
                failed += size
                continue
            nxt = _next_smaller(size)
            if nxt is None:
                if size > 1:
                    for j in range(size):
                        queue.append(cur[j:j+1])
                    continue
                failed += 1
                continue
            for j in range(0, size, nxt):
                queue.append(cur[j:j+nxt])

    return saved, failed
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/lib/test_batch_fallback.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/batch_fallback.py tests/lib/test_batch_fallback.py
git commit -m "feat: lib/batch_fallback — 배치 사이즈 fallback helper 추출"
```

---

## Task 2: tier1_embedder 가 helper 사용하도록 리팩터

**Files:**
- Modify: `scripts/tier1_embedder.py`
- Modify: `tests/test_tier1_embedder.py` (테스트 유지 — 동작 동일)

**Why:** helper 추출 이후 tier1 이 기준 구현이 되어야 tier2/smart_batch 가 confident 하게 쓸 수 있음.

- [ ] **Step 1: save_embeddings_with_fallback 을 helper 호출로 대체**

기존 로직을 삭제하고:
```python
from scripts.lib.batch_fallback import save_with_size_fallback

def save_embeddings_with_fallback(sb, book_ids, embeddings, dry_run=False):
    if len(book_ids) != len(embeddings):
        raise ValueError(...)

    paired = list(zip(book_ids, embeddings))
    def saver(chunk):
        chunk_ids = [p[0] for p in chunk]
        chunk_embs = [p[1] for p in chunk]
        save_embeddings_chunk(sb, chunk_ids, chunk_embs, dry_run=dry_run)
    return save_with_size_fallback(
        paired, saver,
        fallback_sizes=BATCH_SIZE_FALLBACKS,
        is_timeout=_is_statement_timeout,
    )
```

- [ ] **Step 2: 기존 테스트 전부 pass 확인**

```bash
python3 -m pytest tests/test_tier1_embedder.py -v
```
Expected: 14/14 (기존과 동일).

- [ ] **Step 3: Commit**

```bash
git add scripts/tier1_embedder.py
git commit -m "refactor: tier1_embedder 가 lib/batch_fallback helper 사용"
```

---

## Task 3: tier2_embedder 하드닝

**Files:**
- Modify: `scripts/tier2_embedder.py`
- Create: `tests/test_tier2_embedder.py`

**Why:** book_embeddings 에 vector 컬럼 upsert — statement_timeout 위험 높음.

- [ ] **Step 1: 기존 코드 읽기**

`scripts/tier2_embedder.py` 의 batch 루프, `save_*` 함수, main() 확인.

- [ ] **Step 2: Hard import + helper 적용 + 카운터 + exit code**

tier1_embedder 패턴 그대로:
- `try/except ImportError` 제거 → `from lib.retry import with_retry`
- batch 루프에서 `save_with_size_fallback` 호출
- `total_saved`, `total_failed` 추적
- `main()` 이 `sys.exit(1)` if `total_failed > 0`

- [ ] **Step 3: Tests**

tier1_embedder 의 exit code 테스트 패턴 재사용. `compose_embedding_text` / `save_embeddings_*` 가 tier2 에 있으면 동일한 패턴으로 covers.

- [ ] **Step 4: Commit**

```bash
git add scripts/tier2_embedder.py tests/test_tier2_embedder.py
git commit -m "fix: tier2_embedder 하드닝 (helper fallback + exit code + tests)"
```

---

## Task 4: taste_recomputer 하드닝

**Files:**
- Modify: `scripts/taste_recomputer.py`
- Create/extend: `tests/test_taste_recomputer.py`

**Why:** user_taste_vectors 가 vector 컬럼 + chunking 없음. 가장 위험.

- [ ] **Step 1: 기존 코드 읽기**

특히:
- `user_taste_vectors` delete + insert 패턴
- `_refresh_confidence` 의 silent `except Exception: pass` 위치
- main() 흐름

- [ ] **Step 2: chunking + helper 적용**

user 당 vector 가 몇 개인지 확인 후 chunk 크기 결정. 보통은 kmeans 결과 cluster 수 (~10-50개). 한 user 단위 insert 가 이미 작으면 user 별로 묶어서 fallback 적용.

- [ ] **Step 3: `_refresh_confidence` silent pass 제거**

`except Exception: pass` 를 `except Exception as e: self.stats["errors"] += 1; print(...)` 로 변경.

- [ ] **Step 4: Exit code + tests**

tier1 패턴.

- [ ] **Step 5: Commit**

```bash
git add scripts/taste_recomputer.py tests/test_taste_recomputer.py
git commit -m "fix: taste_recomputer 하드닝 (chunking + silent pass 제거 + exit code)"
```

---

## Task 5: smart_batch_collector 하드닝

**Files:**
- Modify: `scripts/smart_batch_collector.py`
- Create: `tests/test_smart_batch_collector.py`

**Why:** per-row fallback 이 silent `except: pass` — 전체가 silent drop 의심.

- [ ] **Step 1: 기존 코드 읽기**

L204-205 근처의 per-row fallback 확인. BATCH_SIZE=50 upsert 실패 시 어떻게 처리되는지.

- [ ] **Step 2: silent pass → 카운터**

`except: pass` 를 카운터 증가 + 로그로 변경. 필요 시 `save_with_size_fallback` helper 로 대체.

- [ ] **Step 3: Exit code + tests**

- [ ] **Step 4: Commit**

```bash
git add scripts/smart_batch_collector.py tests/test_smart_batch_collector.py
git commit -m "fix: smart_batch_collector silent pass 제거 + exit code"
```

---

## Task 6: v3_reason_extract 하드닝 (최소)

**Files:**
- Modify: `scripts/v3_reason_extract.py`
- Modify/create: `tests/test_v3_reason_extract.py`

**Why:** 이미 robust — 오직 exit code 만 빠져있음.

- [ ] **Step 1: main() 이 stats 기반으로 exit code 반환**

```python
if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Tests + Commit**

```bash
git commit -m "fix: v3_reason_extract exit code 전파"
```

---

## Task 7: batch_enricher 하드닝

**Files:**
- Modify: `scripts/batch_enricher.py`
- Create: `tests/test_batch_enricher.py`

**Why:** text-only update 라 Critical 은 아니지만 silent ImportError + no exit code.

- [ ] **Step 1: Hard import + exit code + tests + commit**

---

## Task 8: data4library_collector 하드닝

**Files:**
- Modify: `scripts/data4library_collector.py`
- Create: `tests/test_data4library_collector.py`

- [ ] **Step 1: Hard import + exit code + tests + commit**

---

## Task 9: known-issues.md KI-002 제거

**Files:**
- Modify: `docs/superpowers/known-issues.md`

- [ ] **Step 1: KI-002 섹션 삭제**
- [ ] **Step 2: Commit**

```bash
git commit -m "docs: KI-002 해결 (파이프라인 밖 with_retry 사용자 6개 하드닝)"
```

---

## Task 10: PR 생성

- [ ] **Step 1: Push + PR**

```bash
gh auth switch -u hyhuh0910
git push -u origin fix/audit-nonpipeline-with-retry
gh pr create --title "fix: 파이프라인 밖 with_retry 사용자 6개 하드닝 (KI-002)" ...
```

---

## Self-Review

**Spec coverage:**
- Helper 추출 ✅ Task 1
- tier1 리팩터 ✅ Task 2
- Critical 3개 (tier2, taste, smart_batch) ✅ Task 3-5
- Important 3개 (v3_reason, batch_enricher, data4library) ✅ Task 6-8
- 문서 정리 + PR ✅ Task 9-10

**주의 사항:**
- Task 1 helper 는 일반화가 충분해야 4개 caller 모두 쓸 수 있음 — zip 으로 pair 묶는 패턴 권장
- taste_recomputer (Task 4) 는 자체 도메인 로직 있음 — helper 가 안 맞으면 script-local fallback 도 허용
- 각 script 의 기존 stats 키 네이밍 존중 (기존 호출자 호환)
- **6개 script × 각 테스트 → 크기 M (1-2일)**. 필요 시 Phase 별로 PR 나눠도 됨
- `docs/superpowers/known-issues.md` KI-002 의 세부 항목 (파일 위치/라인) 활용

**예상 소요:** 1-2일 (Phase A,B 우선, Phase C 는 별도 PR 가능).
