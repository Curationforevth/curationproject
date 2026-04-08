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
from typing import List, Optional

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
) -> List[StepResult]:
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

    results: List[StepResult] = []
    for step in steps_to_run:
        r = run_step(step, limit=limit, dry_run=dry_run)
        results.append(r)
        if not r.success:
            print(f"\n✗ {step.name} 실패 (returncode={r.returncode}). 체인 중단.", file=sys.stderr)
            break
    return results


def print_summary(results: List[StepResult]):
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


def _count_not_null(sb, table: str, col: str) -> int:
    """Count rows where col IS NOT NULL."""
    return (
        sb.table(table)
        .select("id", count="exact")
        .not_.is_(col, "null")
        .limit(1)
        .execute()
        .count
    )


def _count_missing(sb, table: str, have_col: str, missing_col: str) -> int:
    """Count rows where have_col IS NOT NULL AND missing_col IS NULL."""
    return (
        sb.table(table)
        .select("id", count="exact")
        .not_.is_(have_col, "null")
        .is_(missing_col, "null")
        .limit(1)
        .execute()
        .count
    )


def _count_total(sb, table: str) -> int:
    """Count all rows in a table."""
    return sb.table(table).select("id", count="exact").limit(1).execute().count


def collect_status(sb) -> dict:
    """Query DB for counts at each pipeline stage."""
    return {
        "with_loan_count": _count_not_null(sb, "books", "loan_count"),
        "missing_rich_description": _count_missing(sb, "books", "loan_count", "rich_description"),
        "with_rich_description": _count_not_null(sb, "books", "rich_description"),
        "with_v3_vectors": _count_total(sb, "book_v3_vectors"),
        "with_embeddings": _count_total(sb, "book_embeddings"),
    }


def print_status(status: dict):
    print("\n=== Pipeline Status ===")
    print(f"  loan_count 있는 책 (정보나루 수집분):    {status['with_loan_count']:>6}")
    print(f"  그중 rich_description 없음 (pending):    {status['missing_rich_description']:>6}")
    print(f"  rich_description 있음:                    {status['with_rich_description']:>6}")
    print(f"  book_v3_vectors:                           {status['with_v3_vectors']:>6}")
    print(f"  book_embeddings:                           {status['with_embeddings']:>6}")


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
