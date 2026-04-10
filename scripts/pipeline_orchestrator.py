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


# DB 검증 임계값: (post - pre) 가 기대치 * threshold 이상이어야 성공으로 집계
# 0.9 = "예상 처리량의 90% 이상 반영됐어야 성공"
PROGRESS_THRESHOLD = 0.9


def _pending_for_step(step_name: str, status: dict) -> Optional[int]:
    """각 step 이 실행 전 '처리해야 할' 대략적 row/book 수를 추정.

    이 값은 두 가지 용도로 쓰임:
      1. 0진전 감지 (pending > 0 인데 delta == 0 → fail) — 정확도 무관
      2. Ratio 검증 (delta / expected < 0.9 → fail) — `step.ratio_verifiable=True` 만

    reason_extractor 는 row(reasons) 와 book(v3_vectors) 단위 혼재라
    1번에만 사용. ratio 검증은 PipelineStep.ratio_verifiable=False 로 꺼져있다.

    Returns:
        pending 추정값, 또는 계산 불가 시 None.
    """
    try:
        if step_name == "yes24_scraper":
            return status.get("missing_rich_description")
        if step_name == "v3_vectors":
            return max(0, status.get("with_rich_description", 0) - status.get("with_v3_vectors", 0))
        if step_name == "reason_extractor":
            # C1 (H1): '/ 13' 추정 대신 distinct book_id 사용. v3 는 book 단위
            # book_love_reasons 는 row 단위라서 row/13 나눗셈은 noise 큼.
            v3 = status.get("with_v3_vectors", 0)
            distinct = status.get("with_reasons_distinct_books", 0)
            return max(0, v3 - distinct)
        if step_name == "tier1_embedder":
            return max(0, status.get("with_rich_description", 0) - status.get("with_embeddings", 0))
    except (TypeError, KeyError):
        return None
    return None


@dataclass
class StepResult:
    name: str
    success: bool
    returncode: int
    error: Optional[str]
    # DB 검증 결과 (검증 안 한 step 은 None)
    progress_delta: Optional[int] = None
    progress_expected: Optional[int] = None
    progress_warning: Optional[str] = None
    # 인프라 진단: pre snapshot 자체가 실패했는지. orchestrator 가
    # 연속 실패 감지로 abort 결정할 때 쓴다.
    pre_snapshot_failed: bool = False


def run_step(
    step: PipelineStep,
    limit: Optional[int],
    dry_run: bool,
    sb=None,
) -> StepResult:
    """Execute a single step as subprocess, with optional DB progress verification.

    sb: supabase client (옵션). 주어지면 step 실행 전/후로 progress_counter 를
        snapshot 해서 실제 DB 에 변화가 있었는지 검증한다. exit code 만 보고
        성공을 판단하면 내부 swallow 된 drop 을 놓치기 때문.
    """
    cmd = build_command(step, limit=limit, dry_run=dry_run)
    cwd = os.path.join(REPO, step.cwd) if step.cwd else REPO
    print(f"\n{'=' * 60}")
    print(f"▶ STEP: {step.name}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"  cwd: {cwd}")
    print('=' * 60)

    # Pre snapshot
    pre_count = None
    pending_before = None
    pre_snapshot_failed = False
    if sb is not None and step.progress_counter and not dry_run:
        try:
            status = collect_status(sb)
            pre_count = status.get(step.progress_counter)
            pending_before = _pending_for_step(step.name, status)
        except Exception as e:
            pre_snapshot_failed = True
            print(f"  ⚠ pre snapshot 실패: {e} — 이 step 의 DB 검증은 스킵됩니다")

    try:
        proc = subprocess.run(cmd, cwd=cwd, check=False)
    except (OSError, FileNotFoundError) as e:
        return StepResult(step.name, False, -1, str(e))

    ok = proc.returncode == 0
    error = None if ok else f"exit={proc.returncode}"

    # Post snapshot + 검증
    progress_delta = None
    progress_expected = None
    progress_warning = None
    if ok and sb is not None and step.progress_counter and pre_count is not None:
        try:
            post = collect_status(sb)
            post_count = post.get(step.progress_counter)
            progress_delta = post_count - pre_count

            # 기대치 계산:
            #   - limit 과 pending_before 모두 있으면 작은 쪽
            #   - 하나만 있으면 그것
            #   - 둘 다 없으면 None (ratio 검증 스킵)
            if limit is not None and pending_before is not None:
                progress_expected = min(limit, pending_before)
            elif limit is not None:
                progress_expected = limit
            elif pending_before is not None:
                progress_expected = pending_before
            else:
                progress_expected = None

            # 검증 2단계로 분리:
            #   (A) 0진전 감지 — pending_before 만 있어도 동작.
            #       reason_extractor 처럼 expected 가 row/book 단위 혼재로
            #       부정확해도, "처리할 것이 있는데 한 권도 늘지 않음" 은 항상 사고.
            #   (B) Ratio 검증 — progress_expected 가 정확할 때만 (정확도 보호)
            #
            # (A) 가 (B) 전에 와야 한다: 0진전이면 (B) 의 ratio 계산도 의미 없음.
            if (
                progress_delta == 0
                and pending_before is not None
                and pending_before > 0
            ):
                progress_warning = (
                    f"DB 진전 0 — 처리 대기 {pending_before}건 있었지만 "
                    "한 건도 반영되지 않음 (silent drop 의심)"
                )
                ok = False
                error = progress_warning
            elif (
                progress_expected is not None
                and progress_expected > 0
                and step.ratio_verifiable
            ):
                ratio = progress_delta / progress_expected
                if ratio < PROGRESS_THRESHOLD:
                    progress_warning = (
                        f"DB 진전이 기대치 미달: {progress_delta}/{progress_expected} "
                        f"({ratio*100:.0f}% < {PROGRESS_THRESHOLD*100:.0f}%)"
                    )
                    ok = False
                    error = progress_warning
        except Exception as e:
            print(f"  ⚠ post snapshot 실패: {e} — DB 검증 스킵")
    elif pre_snapshot_failed and ok:
        # Pre snapshot 이 실패했는데 subprocess 는 성공 — 검증 없이 통과시키되
        # summary 에서 보이도록 warning 남김
        progress_warning = "pre snapshot 실패로 DB 검증 생략됨"

    return StepResult(
        name=step.name,
        success=ok,
        returncode=proc.returncode,
        error=error,
        progress_delta=progress_delta,
        progress_expected=progress_expected,
        progress_warning=progress_warning,
        pre_snapshot_failed=pre_snapshot_failed,
    )


def run_pipeline(
    limit: Optional[int],
    dry_run: bool,
    from_step: Optional[str] = None,
    only_step: Optional[str] = None,
    sb=None,
) -> List[StepResult]:
    """Run the pipeline. Stops on first failure.

    from_step: skip steps before this one
    only_step: run only this one step (mutually exclusive with from_step)
    sb: supabase client 를 주면 각 step 의 DB 진전까지 검증 (권장).
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
    consecutive_pre_snapshot_fails = 0
    for step in steps_to_run:
        r = run_step(step, limit=limit, dry_run=dry_run, sb=sb)
        results.append(r)

        # KI-006: pre snapshot 이 연속으로 실패하면 인프라 이슈 (DB 다운,
        # 권한, 네트워크) 가 의심됨. 매 step 마다 같은 에러를 반복하지 말고
        # 2회 연속이면 orchestrator 자체를 중단해서 운영자가 빨리 인지하게.
        if r.pre_snapshot_failed:
            consecutive_pre_snapshot_fails += 1
        else:
            consecutive_pre_snapshot_fails = 0

        if not r.success:
            print(
                f"\n✗ {step.name} 실패 (returncode={r.returncode}"
                f"{', ' + r.progress_warning if r.progress_warning else ''}). "
                "체인 중단.",
                file=sys.stderr,
            )
            break

        if consecutive_pre_snapshot_fails >= 2:
            print(
                f"\n⛔ pre snapshot 이 {consecutive_pre_snapshot_fails}회 "
                "연속 실패 — 인프라 이슈로 추정. orchestrator 중단.",
                file=sys.stderr,
            )
            break
    return results


def print_summary(results: List[StepResult]):
    print(f"\n{'=' * 60}")
    print("PIPELINE SUMMARY")
    print('=' * 60)
    for r in results:
        status = "✓" if r.success else "✗"
        progress = ""
        if r.progress_delta is not None:
            if r.progress_expected is not None:
                progress = f"  (+{r.progress_delta}/{r.progress_expected})"
            else:
                progress = f"  (+{r.progress_delta})"
        print(f"  {status} {r.name:20} rc={r.returncode}{progress}")
        if r.progress_warning:
            print(f"      ⚠ {r.progress_warning}")
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


def _count_total(sb, table: str, pk: str = "id") -> int:
    """Count all rows in a table. pk = primary-key column to select."""
    return sb.table(table).select(pk, count="exact").limit(1).execute().count


def _count_distinct_book_id_in_reasons(sb) -> int:
    """C1 (H1): book_love_reasons 에서 distinct book_id 수.

    supabase-py 는 COUNT(DISTINCT ...) 를 직접 지원하지 않아 pagination 으로
    book_id 만 pull 해 in-memory set 크기 집계. row/13 추정보다 훨씬 정확.
    """
    seen: set = set()
    offset = 0
    while True:
        res = (
            sb.table("book_love_reasons")
            .select("book_id")
            .range(offset, offset + 999)
            .execute()
        )
        if not res.data:
            break
        for r in res.data:
            seen.add(r["book_id"])
        if len(res.data) < 1000:
            break
        offset += 1000
    return len(seen)


def collect_status(sb) -> dict:
    """Query DB for counts at each pipeline stage.

    키는 PipelineStep.progress_counter 와 일치해야 한다.
    """
    return {
        "with_loan_count": _count_not_null(sb, "books", "loan_count"),
        "missing_rich_description": _count_missing(sb, "books", "loan_count", "rich_description"),
        "with_rich_description": _count_not_null(sb, "books", "rich_description"),
        "with_v3_vectors": _count_total(sb, "book_v3_vectors", pk="book_id"),
        "with_reasons": _count_total(sb, "book_love_reasons"),
        "with_reasons_distinct_books": _count_distinct_book_id_in_reasons(sb),
        "with_embeddings": _count_total(sb, "book_embeddings"),
    }


def print_status(status: dict):
    print("\n=== Pipeline Status ===")
    print(f"  loan_count 있는 책 (정보나루 수집분):    {status['with_loan_count']:>6}")
    print(f"  그중 rich_description 없음 (pending):    {status['missing_rich_description']:>6}")
    print(f"  rich_description 있음:                    {status['with_rich_description']:>6}")
    print(f"  book_v3_vectors:                           {status['with_v3_vectors']:>6}")
    print(f"  book_love_reasons (rows):                  {status['with_reasons']:>6}")
    print(f"  book_love_reasons (distinct books):        {status.get('with_reasons_distinct_books', 0):>6}")
    print(f"  book_embeddings:                           {status['with_embeddings']:>6}")


def _env_fingerprint(path: str) -> str:
    """C4 (H4): .env 의 SUPABASE_URL 라인 hash — prefix 12자.

    curation/.env 와 recommendation-server/.env 가 같은 DB 를 가리키는지
    startup 에서 빠르게 시각 확인. key 자체는 찍지 않는다.
    """
    import hashlib
    try:
        with open(path, "rb") as f:
            content = f.read()
        for line in content.decode("utf-8", errors="ignore").splitlines():
            if line.startswith("SUPABASE_URL="):
                return hashlib.sha256(line.encode()).hexdigest()[:12]
    except OSError:
        return "n/a"
    return "n/a"


def _warn_if_env_drift():
    """curation/.env 와 recommendation-server/.env 의 SUPABASE_URL 비교."""
    curation_env = os.path.join(REPO, ".env")
    rec_env = os.path.join(REPO, "recommendation-server", ".env")
    c_hash = _env_fingerprint(curation_env)
    r_hash = _env_fingerprint(rec_env)
    print(f"[env] curation/.env SUPABASE_URL hash: {c_hash}")
    print(f"[env] rec-server/.env SUPABASE_URL hash: {r_hash}")
    if os.path.exists(rec_env) and c_hash != "n/a" and r_hash != "n/a" and c_hash != r_hash:
        print(
            "⚠ curation 과 recommendation-server 의 SUPABASE_URL 이 다릅니다.",
            file=sys.stderr,
        )
        print(
            "  build_index 가 orchestrator 와 다른 DB 를 볼 수 있습니다.",
            file=sys.stderr,
        )


def _make_supabase_client():
    """Lazy supabase client 생성 (env 로드 포함)."""
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv(os.path.join(REPO, ".env"))
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )


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

    _warn_if_env_drift()

    if args.status:
        sb = _make_supabase_client()
        print_status(collect_status(sb))
        return

    if args.step and args.from_step:
        print("ERROR: --step 과 --from 은 동시에 사용 불가", file=sys.stderr)
        sys.exit(2)

    # dry-run 이 아니면 DB 검증을 위해 supabase client 주입.
    sb = None if args.dry_run else _make_supabase_client()

    results = run_pipeline(
        limit=args.limit,
        dry_run=args.dry_run,
        from_step=args.from_step,
        only_step=args.step,
        sb=sb,
    )
    print_summary(results)
    if any(not r.success for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
