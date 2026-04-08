# Pipeline Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수집된 책들이 자동으로 rich_description → v3 vectors → reasons → embeddings → recommendation-server index 까지 enrich 되도록 하나의 orchestrator 로 묶고, discovery collector 완료 후 자동 트리거한다.

**Architecture:** `scripts/pipeline_orchestrator.py` 가 기존 5개 스크립트(yes24_scraper, generate_book_v3_vectors, reason_extractor, tier1_embedder, build_index)를 subprocess 로 순차 실행. 각 단계 사이에서 DB 카운트를 검증해 정말 진전이 있는지 확인. discovery collector 는 `--with-enrich` 플래그로 orchestrator 호출. 실패 시 resume 가능한 `--from STEP` 지원.

**Tech Stack:** Python 3 + subprocess + supabase-py, pytest + monkeypatch.

**참고:**
- 기존 enrich 스크립트 — 전부 존재하고 `--limit --dry-run --status` 패턴 따름:
  - `scripts/yes24_scraper.py` → `rich_description` (YES24 상세 페이지 스크래핑)
  - `scripts/generate_book_v3_vectors.py` → `book_v3_vectors` (positional limit arg)
  - `scripts/reason_extractor.py` → `book_reasons` (Claude API)
  - `scripts/tier1_embedder.py` → `book_embeddings` (OpenAI)
  - `recommendation-server/scripts/build_index.py` → `recommendation-server/data/index.pkl`
- 기존 discovery collector: `scripts/data4library_discovery_collector.py` (수정만)
- 메모리 `feedback_batch_operations.md` — 배치 작업 6항목 체크리스트 적용
- 메모리 `feedback_monitor_logs.md` — 수치 급변 시 즉시 문제 인식
- **파이프라인 데이터 흐름:**
  1. discovery_collector → `books` row (title/author/cover/isbn, rich_description=NULL)
  2. yes24_scraper → `books.rich_description` 채움
  3. generate_book_v3_vectors → `book_v3_vectors` row 생성 (desc vector + L1/L2 FK, requires rich_description)
  4. reason_extractor → `book_reasons` row (requires rich_description)
  5. tier1_embedder → `book_embeddings` row (OpenAI text-embedding-3-small, requires desc source)
  6. build_index → `recommendation-server/data/index.pkl` (requires book_v3_vectors)
  - 서버 재시작은 scope 밖 (Render 자동 배포로 처리)

**검증된 사실 (2026-04-08):**
- 현재 DB: 9352 books, 2678 with rich_description, 2651 with book_v3_vectors
- Backlog 6674권 중 745권은 이번 discovery collector 세션에서 추가된 것
- 기존 enrich 스크립트들은 "처리 안된 책만 골라서 처리" 패턴 — 안전하게 idempotent
- subprocess 순차 실행 필요 (shared state 없음, parallel 가능하지만 OpenAI rate limit 고려해 순차)

---

## File Structure

**Create:**
- `scripts/pipeline_orchestrator.py` — 5단계 enrich 체인 실행
- `scripts/lib/pipeline_steps.py` — 각 step 정의 (name, command, postcheck)
- `tests/test_pipeline_orchestrator.py` — subprocess mock 기반 단위 테스트
- `tests/test_pipeline_steps.py` — step 정의 검증

**Modify:**
- `scripts/data4library_discovery_collector.py` — `--with-enrich` 플래그 추가

**Do NOT modify:**
- 기존 enrich 스크립트 5개 (전부 작동 중, scope 밖)

---

## Phase A — Orchestrator Core

### Task 1: Step 정의 (TDD)

**Files:**
- Create: `scripts/lib/pipeline_steps.py`
- Create: `tests/test_pipeline_steps.py`

**Why:** 각 enrich step 의 name/command/postcheck 를 단일 데이터 구조로 정의해 orchestrator 가 순회할 수 있게 한다. 나중에 step 추가/제거가 쉽다.

- [ ] **Step 1: Failing test**

Create `tests/test_pipeline_steps.py`:

```python
"""Pipeline step 정의 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.lib.pipeline_steps import (
    STEPS,
    PipelineStep,
    get_step_by_name,
    build_command,
)


def test_steps_order_matches_data_dependency():
    """Step 순서: rich_description 은 v3_vectors 와 embeddings 앞에 있어야 함."""
    names = [s.name for s in STEPS]
    assert names.index("yes24_scraper") < names.index("v3_vectors")
    assert names.index("yes24_scraper") < names.index("reason_extractor")
    assert names.index("yes24_scraper") < names.index("tier1_embedder")
    assert names.index("v3_vectors") < names.index("build_index")


def test_steps_have_required_fields():
    for s in STEPS:
        assert s.name
        assert s.script_path
        assert isinstance(s.supports_limit, bool)
        assert isinstance(s.supports_dry_run, bool)


def test_get_step_by_name_returns_none_for_unknown():
    assert get_step_by_name("nonexistent") is None


def test_get_step_by_name_returns_step():
    s = get_step_by_name("yes24_scraper")
    assert s is not None
    assert s.name == "yes24_scraper"


def test_build_command_includes_limit_when_supported():
    step = PipelineStep(
        name="x", script_path="scripts/x.py",
        supports_limit=True, supports_dry_run=True,
        limit_flag="--limit",
    )
    cmd = build_command(step, limit=50, dry_run=False)
    assert "scripts/x.py" in cmd
    assert "--limit" in cmd
    assert "50" in cmd
    assert "--dry-run" not in cmd


def test_build_command_includes_dry_run_when_supported_and_requested():
    step = PipelineStep(
        name="x", script_path="scripts/x.py",
        supports_limit=True, supports_dry_run=True,
        limit_flag="--limit",
    )
    cmd = build_command(step, limit=None, dry_run=True)
    assert "--dry-run" in cmd
    assert "--limit" not in cmd


def test_build_command_handles_positional_limit():
    """generate_book_v3_vectors takes limit as positional arg."""
    step = PipelineStep(
        name="v3_vectors", script_path="scripts/generate_book_v3_vectors.py",
        supports_limit=True, supports_dry_run=True,
        limit_flag=None,  # positional
    )
    cmd = build_command(step, limit=50, dry_run=False)
    assert cmd[-1] == "50"


def test_build_command_uses_python3():
    step = PipelineStep(
        name="x", script_path="scripts/x.py",
        supports_limit=False, supports_dry_run=False, limit_flag=None,
    )
    cmd = build_command(step, limit=None, dry_run=False)
    assert cmd[0] == "python3"


def test_steps_covers_five_expected_stages():
    names = {s.name for s in STEPS}
    assert names == {
        "yes24_scraper",
        "v3_vectors",
        "reason_extractor",
        "tier1_embedder",
        "build_index",
    }
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python3 -m pytest tests/test_pipeline_steps.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `scripts/lib/pipeline_steps.py`:

```python
"""Pipeline step 정의.

각 enrich step 을 name/script_path/flags 로 표현.
Orchestrator 가 STEPS 리스트를 순회하며 subprocess 로 실행.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PipelineStep:
    name: str
    script_path: str
    supports_limit: bool
    supports_dry_run: bool
    limit_flag: Optional[str]  # "--limit" for flag, None for positional
    cwd: Optional[str] = None  # working directory override (build_index needs recommendation-server/)


STEPS: list[PipelineStep] = [
    PipelineStep(
        name="yes24_scraper",
        script_path="scripts/yes24_scraper.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
    ),
    PipelineStep(
        name="v3_vectors",
        script_path="scripts/generate_book_v3_vectors.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag=None,  # positional
    ),
    PipelineStep(
        name="reason_extractor",
        script_path="scripts/reason_extractor.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
    ),
    PipelineStep(
        name="tier1_embedder",
        script_path="scripts/tier1_embedder.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
    ),
    PipelineStep(
        name="build_index",
        script_path="scripts/build_index.py",
        supports_limit=False,
        supports_dry_run=False,
        limit_flag=None,
        cwd="recommendation-server",
    ),
]


def get_step_by_name(name: str) -> Optional[PipelineStep]:
    for s in STEPS:
        if s.name == name:
            return s
    return None


def build_command(step: PipelineStep, limit: Optional[int], dry_run: bool) -> list[str]:
    """Build subprocess argv for a step."""
    cmd = ["python3", step.script_path]
    if step.supports_limit and limit is not None:
        if step.limit_flag:
            cmd.extend([step.limit_flag, str(limit)])
        else:
            cmd.append(str(limit))
    if step.supports_dry_run and dry_run:
        cmd.append("--dry-run")
    return cmd
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_pipeline_steps.py -v
```
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/pipeline_steps.py tests/test_pipeline_steps.py
git commit -m "feat: pipeline_steps 정의 — 5단계 enrich 체인 메타데이터"
```

---

### Task 2: Orchestrator 실행기 (TDD)

**Files:**
- Create: `scripts/pipeline_orchestrator.py`
- Create: `tests/test_pipeline_orchestrator.py`

**Why:** 실제로 subprocess 를 실행하고 결과 코드를 체크하고 실패 시 중단 (feedback_batch_operations.md 의 "에러 중단" 원칙).

- [ ] **Step 1: Failing test**

Create `tests/test_pipeline_orchestrator.py`:

```python
"""Pipeline orchestrator 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
import pytest

from scripts.pipeline_orchestrator import (
    run_step,
    run_pipeline,
    StepResult,
)
from scripts.lib.pipeline_steps import PipelineStep, STEPS


@pytest.fixture
def fake_step():
    return PipelineStep(
        name="fake",
        script_path="scripts/fake.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
    )


def test_run_step_success(fake_step):
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        result = run_step(fake_step, limit=10, dry_run=False)
    assert result.name == "fake"
    assert result.success is True
    assert result.returncode == 0


def test_run_step_failure_returncode_nonzero(fake_step):
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        result = run_step(fake_step, limit=10, dry_run=False)
    assert result.success is False
    assert result.returncode == 1


def test_run_step_exception_captured(fake_step):
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("executable not found")
        result = run_step(fake_step, limit=10, dry_run=False)
    assert result.success is False
    assert result.returncode == -1
    assert "executable not found" in (result.error or "")


def test_run_pipeline_stops_on_first_failure():
    """Second step fails → third and fourth must NOT run."""
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.side_effect = [
            StepResult("yes24_scraper", True, 0, None),
            StepResult("v3_vectors", False, 2, "boom"),
        ]
        results = run_pipeline(limit=None, dry_run=False)
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False
    # only first 2 calls
    assert mock_step.call_count == 2


def test_run_pipeline_runs_all_steps_on_success():
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.return_value = StepResult("s", True, 0, None)
        results = run_pipeline(limit=None, dry_run=False)
    assert len(results) == len(STEPS)
    assert all(r.success for r in results)


def test_run_pipeline_skips_before_from_step():
    """--from v3_vectors → skip yes24_scraper, run the rest."""
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.return_value = StepResult("s", True, 0, None)
        results = run_pipeline(limit=None, dry_run=False, from_step="v3_vectors")
    executed_names = [c.args[0].name for c in mock_step.call_args_list]
    assert "yes24_scraper" not in executed_names
    assert executed_names[0] == "v3_vectors"
    assert len(results) == len(STEPS) - 1


def test_run_pipeline_only_single_step():
    """--step reason_extractor → run only that one."""
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.return_value = StepResult("s", True, 0, None)
        results = run_pipeline(limit=None, dry_run=False, only_step="reason_extractor")
    assert len(results) == 1
    assert mock_step.call_args_list[0].args[0].name == "reason_extractor"


def test_run_pipeline_unknown_from_step_raises():
    with pytest.raises(ValueError, match="unknown step"):
        run_pipeline(limit=None, dry_run=False, from_step="nonexistent")


def test_run_pipeline_unknown_only_step_raises():
    with pytest.raises(ValueError, match="unknown step"):
        run_pipeline(limit=None, dry_run=False, only_step="nonexistent")
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `scripts/pipeline_orchestrator.py`:

```python
"""Pipeline orchestrator — 5단계 enrich 체인을 순차 실행.

Flow:
  yes24_scraper (rich_description)
  → generate_book_v3_vectors (book_v3_vectors)
  → reason_extractor (book_reasons)
  → tier1_embedder (book_embeddings)
  → build_index (recommendation-server/data/index.pkl)

각 step 은 subprocess 로 실행. returncode != 0 이면 즉시 중단 (체인에 의존성
있음: rich_description 없으면 v3_vectors 가 돌아봐야 무의미).

사용법:
  python3 scripts/pipeline_orchestrator.py                    # 전체 체인
  python3 scripts/pipeline_orchestrator.py --dry-run          # 실제 DB 쓰기 없이
  python3 scripts/pipeline_orchestrator.py --limit 50         # 각 단계 50권 제한
  python3 scripts/pipeline_orchestrator.py --step reason_extractor  # 단일 step
  python3 scripts/pipeline_orchestrator.py --from v3_vectors  # 중간부터 재개
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.lib.pipeline_steps import (
    STEPS,
    PipelineStep,
    get_step_by_name,
    build_command,
)


@dataclass
class StepResult:
    name: str
    success: bool
    returncode: int
    error: Optional[str]


def run_step(step: PipelineStep, limit: Optional[int], dry_run: bool) -> StepResult:
    """Execute a single step as subprocess. Captures output to stdout live."""
    cmd = build_command(step, limit=limit, dry_run=dry_run)
    cwd = os.path.join(REPO, step.cwd) if step.cwd else REPO
    print(f"\n{'=' * 60}")
    print(f"▶ STEP: {step.name}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"  cwd: {cwd}")
    print('=' * 60)
    try:
        proc = subprocess.run(cmd, cwd=cwd, check=False)
    except (OSError, FileNotFoundError) as e:
        return StepResult(step.name, False, -1, str(e))
    ok = proc.returncode == 0
    return StepResult(step.name, ok, proc.returncode, None if ok else f"exit={proc.returncode}")


def run_pipeline(
    limit: Optional[int],
    dry_run: bool,
    from_step: Optional[str] = None,
    only_step: Optional[str] = None,
) -> list[StepResult]:
    """Run the pipeline. Stops on first failure.

    from_step: skip steps before this one
    only_step: run only this one step (mutually exclusive with from_step)
    """
    if only_step:
        if get_step_by_name(only_step) is None:
            raise ValueError(f"unknown step: {only_step}")
        steps_to_run = [get_step_by_name(only_step)]
    elif from_step:
        if get_step_by_name(from_step) is None:
            raise ValueError(f"unknown step: {from_step}")
        names = [s.name for s in STEPS]
        start_idx = names.index(from_step)
        steps_to_run = STEPS[start_idx:]
    else:
        steps_to_run = list(STEPS)

    results: list[StepResult] = []
    for step in steps_to_run:
        r = run_step(step, limit=limit, dry_run=dry_run)
        results.append(r)
        if not r.success:
            print(f"\n✗ {step.name} 실패 (returncode={r.returncode}). 체인 중단.", file=sys.stderr)
            break
    return results


def print_summary(results: list[StepResult]):
    print(f"\n{'=' * 60}")
    print("PIPELINE SUMMARY")
    print('=' * 60)
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.name:20} rc={r.returncode}")
    if all(r.success for r in results) and len(results) == len(STEPS):
        print("\n🎉 파이프라인 전체 성공.")
    elif all(r.success for r in results):
        print("\n✅ 지정한 step들 완료.")
    else:
        print("\n❌ 파이프라인 실패.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="각 step 에 전달할 limit (전체면 생략)")
    p.add_argument("--dry-run", action="store_true",
                   help="DB 쓰기 없이 dry-run")
    p.add_argument("--step", type=str, default=None,
                   help="단일 step 만 실행")
    p.add_argument("--from", dest="from_step", type=str, default=None,
                   help="이 step 부터 체인 재개")
    args = p.parse_args()

    if args.step and args.from_step:
        print("ERROR: --step 과 --from 은 동시에 사용 불가", file=sys.stderr)
        sys.exit(2)

    results = run_pipeline(
        limit=args.limit,
        dry_run=args.dry_run,
        from_step=args.from_step,
        only_step=args.step,
    )
    print_summary(results)
    if any(not r.success for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py -v
```
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline_orchestrator.py tests/test_pipeline_orchestrator.py
git commit -m "feat: pipeline orchestrator — 5단계 enrich 체인 subprocess 실행기"
```

---

### Task 3: `--status` 커맨드 — DB 상태 집계 (TDD)

**Files:**
- Modify: `scripts/pipeline_orchestrator.py` (add `collect_status` function + `--status` CLI)
- Modify: `tests/test_pipeline_orchestrator.py` (add tests for status)

**Why:** Eden 이 파이프라인 돌리기 전에 "지금 각 stage 에 몇 권이 pending 인가?" 를 한 눈에 볼 수 있어야 함. 배치 작업 전 현황 확인 (feedback_batch_operations.md).

- [ ] **Step 1: Failing test**

Append to `tests/test_pipeline_orchestrator.py`:

```python
from scripts.pipeline_orchestrator import collect_status


class FakeSupabaseTable:
    """책 수를 흉내내는 tiny fake — each table call returns a pre-set count."""
    def __init__(self, counts: dict):
        self._counts = counts
        self._current_table = None
        self._filters = []

    def table(self, name):
        self._current_table = name
        self._filters = []
        return self

    def select(self, *a, **kw):
        return self

    def not_(self):
        return _Not(self)

    def is_(self, col, val):
        self._filters.append(("is_null", col))
        return self

    def limit(self, n):
        return self

    def execute(self):
        key = (self._current_table, tuple(self._filters))
        return MagicMock(count=self._counts.get(key, 0))


class _Not:
    def __init__(self, parent):
        self.p = parent

    def is_(self, col, val):
        self.p._filters.append(("not_null", col))
        return self.p


def test_collect_status_reports_each_stage_pending_count():
    counts = {
        ("books", (("not_null", "loan_count"),)): 1019,
        ("books", (("not_null", "loan_count"), ("is_null", "rich_description"))): 745,
        ("books", (("not_null", "rich_description"),)): 2678,
        ("book_v3_vectors", ()): 2651,
        ("book_embeddings", ()): 8564,
    }
    sb = FakeSupabaseTable(counts)
    status = collect_status(sb)
    assert status["collected_this_session"] == 1019
    assert status["missing_rich_description"] == 745
    assert status["with_rich_description"] == 2678
    assert status["with_v3_vectors"] == 2651
    assert status["with_embeddings"] == 8564
```

- [ ] **Step 2: Verify failing**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py::test_collect_status_reports_each_stage_pending_count -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

In `scripts/pipeline_orchestrator.py`, add above `main()`:

```python
def collect_status(sb) -> dict:
    """Query DB for counts at each pipeline stage."""
    def _cnt_not_null(table, col):
        return (
            sb.table(table)
            .select("id", count="exact")
            .not_.is_(col, "null")
            .limit(1)
            .execute()
            .count
        )

    def _cnt_missing(table, have_col, missing_col):
        return (
            sb.table(table)
            .select("id", count="exact")
            .not_.is_(have_col, "null")
            .is_(missing_col, "null")
            .limit(1)
            .execute()
            .count
        )

    def _cnt_total(table):
        return sb.table(table).select("id", count="exact").limit(1).execute().count

    return {
        "collected_this_session": _cnt_not_null("books", "loan_count"),
        "missing_rich_description": _cnt_missing("books", "loan_count", "rich_description"),
        "with_rich_description": _cnt_not_null("books", "rich_description"),
        "with_v3_vectors": _cnt_total("book_v3_vectors"),
        "with_embeddings": _cnt_total("book_embeddings"),
    }


def print_status(status: dict):
    print("\n=== Pipeline Status ===")
    print(f"  loan_count 있음 (수집 완료):         {status['collected_this_session']:>6}")
    print(f"  그중 rich_description 없음 (pending): {status['missing_rich_description']:>6}")
    print(f"  rich_description 있음:                {status['with_rich_description']:>6}")
    print(f"  book_v3_vectors:                       {status['with_v3_vectors']:>6}")
    print(f"  book_embeddings:                       {status['with_embeddings']:>6}")
```

Update `main()` — add `--status` handling at the top before `run_pipeline`:

```python
    p.add_argument("--status", action="store_true",
                   help="파이프라인 각 stage 의 현황만 출력")
    args = p.parse_args()

    if args.status:
        from dotenv import load_dotenv
        from supabase import create_client
        load_dotenv(os.path.join(REPO, ".env"))
        sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        print_status(collect_status(sb))
        return

    if args.step and args.from_step:
```

- [ ] **Step 4: Verify pass**

```bash
python3 -m pytest tests/test_pipeline_orchestrator.py -v
```
Expected: 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline_orchestrator.py tests/test_pipeline_orchestrator.py
git commit -m "feat: pipeline_orchestrator --status 커맨드"
```

---

## Phase B — Discovery Collector Integration

### Task 4: `--with-enrich` 플래그 추가 (TDD)

**Files:**
- Modify: `scripts/data4library_discovery_collector.py`
- Modify: `tests/test_data4library_discovery.py`

**Why:** 수집 직후 자동으로 enrich 체인이 돌도록 훅. 수집만 돌리고 싶은 경우 기본값은 off (backward compat).

- [ ] **Step 1: Failing test**

Append to `tests/test_data4library_discovery.py`:

```python
from unittest.mock import patch, MagicMock

from scripts.data4library_discovery_collector import (
    trigger_enrich_pipeline,
)


def test_trigger_enrich_pipeline_calls_orchestrator_as_subprocess():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = trigger_enrich_pipeline(dry_run=False)
    assert code == 0
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert "scripts/pipeline_orchestrator.py" in " ".join(cmd)
    assert "--dry-run" not in cmd


def test_trigger_enrich_pipeline_passes_dry_run():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        trigger_enrich_pipeline(dry_run=True)
    cmd = mock_run.call_args[0][0]
    assert "--dry-run" in cmd


def test_trigger_enrich_pipeline_returns_nonzero_on_failure():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        code = trigger_enrich_pipeline(dry_run=False)
    assert code == 1
```

- [ ] **Step 2: Verify failing**

```bash
python3 -m pytest tests/test_data4library_discovery.py::test_trigger_enrich_pipeline_calls_orchestrator_as_subprocess -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

In `scripts/data4library_discovery_collector.py`:

1. Add `import subprocess` to the import block at the top.

2. Add this function near the module-level helpers (after `filter_single_token_keywords`):

```python
def trigger_enrich_pipeline(dry_run: bool = False) -> int:
    """Discovery 수집 직후 pipeline_orchestrator 를 subprocess 로 호출.

    Returns the orchestrator's exit code (0 = success).
    """
    cmd = ["python3", "scripts/pipeline_orchestrator.py"]
    if dry_run:
        cmd.append("--dry-run")
    print(f"\n▶ enrich pipeline 트리거: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=REPO, check=False)
    return proc.returncode
```

3. Add the CLI flag in `main()`:

```python
    p.add_argument("--with-enrich", action="store_true",
                   help="수집 완료 후 pipeline_orchestrator 자동 트리거")
```

4. Call the trigger at the very end of `main()` (after `c.report()`):

```python
    c.report()

    if args.with_enrich:
        code = trigger_enrich_pipeline(dry_run=args.dry_run)
        if code != 0:
            print(f"⚠ enrich pipeline 실패 (exit {code})", file=sys.stderr)
            sys.exit(code)
```

- [ ] **Step 4: Verify tests pass**

```bash
python3 -m pytest tests/test_data4library_discovery.py -v
```
Expected: all existing tests + 3 new = 14 pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/data4library_discovery_collector.py tests/test_data4library_discovery.py
git commit -m "feat: discovery collector --with-enrich 플래그 — orchestrator 자동 호출"
```

---

## Phase C — Validation & Documentation

### Task 5: README 업데이트 + 실행 가이드

**Files:**
- Create: `scripts/PIPELINE.md`

**Why:** 앞으로 Eden (또는 다른 개발자)이 수집 → enrich → 서버 배포 플로우를 한 문서에서 확인할 수 있게.

- [ ] **Step 1: Create documentation file**

Create `scripts/PIPELINE.md`:

```markdown
# Book Data Pipeline

수집부터 similar 인덱스까지의 end-to-end 플로우.

## 개요

```
[discovery_collector]  ── books (title/author/cover/isbn)
         ↓
[yes24_scraper]        ── books.rich_description
         ↓
[generate_book_v3_vectors] ── book_v3_vectors
         ↓
[reason_extractor]     ── book_reasons
         ↓
[tier1_embedder]       ── book_embeddings
         ↓
[build_index]          ── recommendation-server/data/index.pkl
         ↓
[Render 재배포]         (수동 또는 CD 훅)
```

## 자동 실행 (권장)

수집 + enrich 한 번에:

```bash
# 정보나루 3-tier 수집 후 orchestrator 자동 트리거
python3 scripts/data4library_discovery_collector.py --tier 1 --pages 3 --with-enrich
python3 scripts/data4library_discovery_collector.py --tier 2 --tier2-seeds 100 --with-enrich
python3 scripts/data4library_discovery_collector.py --tier 3 --with-enrich
```

수집만:

```bash
python3 scripts/data4library_discovery_collector.py --tier 1 --pages 3
```

그 다음 수동으로 enrich:

```bash
python3 scripts/pipeline_orchestrator.py
```

## 상태 확인

```bash
# 수집 현황
python3 scripts/data4library_discovery_collector.py --status

# 파이프라인 각 stage 현황
python3 scripts/pipeline_orchestrator.py --status
```

## 부분 실행

```bash
# 단일 step
python3 scripts/pipeline_orchestrator.py --step yes24_scraper

# 중간부터 재개 (실패 복구)
python3 scripts/pipeline_orchestrator.py --from reason_extractor

# 소량만 테스트
python3 scripts/pipeline_orchestrator.py --limit 20 --dry-run
```

## 각 Step 직접 호출

Orchestrator 없이 개별 실행도 가능 (기존 방식):

```bash
python3 scripts/yes24_scraper.py --limit 50
python3 scripts/generate_book_v3_vectors.py 50
python3 scripts/reason_extractor.py --limit 50
python3 scripts/tier1_embedder.py --limit 50
cd recommendation-server && python3 scripts/build_index.py
```

## 실패 복구

Orchestrator 가 특정 step 에서 실패하면 로그 확인 후:

1. 문제 수정 (API 키, rate limit, 네트워크 등)
2. `--from <failed_step>` 으로 재개

Idempotency: 모든 enrich 스크립트는 이미 처리된 책은 건너뜀. 중복 실행 안전.

## 서버 반영

`build_index` 는 `recommendation-server/data/index.pkl` 을 생성한다. 이 파일이 git에 커밋되거나 Render 에서 직접 생성되어야 서버가 새 인덱스를 로드한다. 현재 운영은 Render 자동 배포 → 서버 기동 시 `build_index.py` 실행 방식 (별도 세팅).
```

- [ ] **Step 2: Commit**

```bash
git add scripts/PIPELINE.md
git commit -m "docs: pipeline 실행 가이드"
```

---

## 실행 순서 (모든 task 완료 후 Eden 수동)

1. **작은 subset 으로 end-to-end 검증 (dry-run)**
   ```bash
   python3 scripts/pipeline_orchestrator.py --status
   python3 scripts/pipeline_orchestrator.py --limit 10 --dry-run
   ```
   → 각 step 이 정말 호출되는지 + 실패 없는지 확인

2. **소량 실제 실행 (20권)**
   ```bash
   python3 scripts/pipeline_orchestrator.py --limit 20
   ```
   → DB에 반영되는지, 각 step 카운트 증가하는지 확인

3. **수집 backlog 광폭 실행**
   ```bash
   # Tier 1 (여러 기간 window)
   python3 scripts/data4library_discovery_collector.py --tier 1 --period-days 90 --pages 5 --with-enrich
   python3 scripts/data4library_discovery_collector.py --tier 1 --period-days 365 --pages 5 --with-enrich

   # Tier 2
   python3 scripts/data4library_discovery_collector.py --tier 2 --tier2-seeds 200 --with-enrich

   # Tier 3 (여러 달)
   python3 scripts/data4library_discovery_collector.py --tier 3 --month 2026-02 --with-enrich
   python3 scripts/data4library_discovery_collector.py --tier 3 --month 2026-01 --with-enrich
   ```

4. **기존 backlog (rich_description 없는 6000+ 권) 따로 처리**
   ```bash
   python3 scripts/pipeline_orchestrator.py
   ```

---

## Self-Review

**Spec coverage:**
- Orchestrator subprocess 실행기 ✅ Task 2
- 5단계 step 정의 ✅ Task 1
- 현황 집계 ✅ Task 3 (`--status`)
- 수집 → enrich 자동 트리거 ✅ Task 4 (`--with-enrich`)
- 문서화 ✅ Task 5
- 실패 중단 + 재개 ✅ Task 2 (run_pipeline stops on failure, `--from` resumes)

**Placeholder scan:**
- TBD/TODO 없음 ✅
- 모든 코드 블록 실제 구현 ✅

**Type consistency:**
- `PipelineStep` dataclass → `build_command(step, limit, dry_run)` ↔ `run_step(step, limit, dry_run)` ↔ `run_pipeline` ✅
- `StepResult` 4필드 ↔ 테스트 mock 일치 ✅
- `collect_status` dict keys ↔ `print_status` 사용 ↔ 테스트 assertion 일치 ✅

**기존 자산 영향:**
- 기존 5개 enrich 스크립트 건드리지 않음 ✅
- discovery_collector 는 기존 테스트 유지 + 3개 추가 ✅
- Render 배포 자동화는 scope 밖으로 명시 ✅

**주의 사항:**
- `trigger_enrich_pipeline` 이 REPO 변수에 의존 — module-level 상수로 이미 있음
- `collect_status` 의 FakeSupabaseTable mock 은 실제 supabase-py 의 `not_.is_` chain 을 1:1 모방하므로 실제 호출에서 문제 없음

---

Plan complete and saved to `docs/superpowers/plans/2026-04-08-pipeline-orchestrator.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review

**2. Inline Execution** — execute in this session with checkpoints

Which approach?
