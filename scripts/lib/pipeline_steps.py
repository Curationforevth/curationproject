"""Pipeline step 정의.

각 enrich step 을 name/script_path/flags 로 표현.
Orchestrator 가 STEPS 리스트를 순회하며 subprocess 로 실행.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class PipelineStep:
    name: str
    script_path: str
    supports_limit: bool
    supports_dry_run: bool
    limit_flag: Optional[str]  # "--limit" for flag, None for positional
    cwd: Optional[str] = None  # working directory override (build_index needs recommendation-server/)
    # DB 검증용 — 이 step 이 성공하면 `collect_status()` 의 어떤 키가 증가해야 하는가.
    # None 이면 DB 검증 스킵 (예: build_index 는 파일 산출물이라 DB 카운터가 없음).
    progress_counter: Optional[str] = None
    # Ratio 검증 활성 여부.
    # True 면 (delta / progress_expected < PROGRESS_THRESHOLD) 시 실패.
    # False 면 ratio 검증은 건너뛰고 0진전 감지만 동작.
    # 단위가 row/book 혼재라 pending 추정이 부정확한 step (예: reason_extractor) 은 False.
    # **fail-safe 기본값: False** — 새 step 추가 시 명시적으로 True 로 설정해야 ratio 검증 켜짐.
    ratio_verifiable: bool = False


STEPS: List[PipelineStep] = [
    PipelineStep(
        name="yes24_scraper",
        script_path="scripts/yes24_scraper.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
        progress_counter="with_rich_description",
        ratio_verifiable=True,  # 책 단위 정확
    ),
    PipelineStep(
        name="v3_vectors",
        script_path="scripts/generate_book_v3_vectors.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag=None,  # positional
        progress_counter="with_v3_vectors",
        ratio_verifiable=True,  # 책 단위 정확
    ),
    PipelineStep(
        # A3: reason_extractor (v1) → v3_reason_extract 전환.
        # name 은 호환을 위해 그대로 유지 (로그/테스트/체크포인트 키).
        name="reason_extractor",
        script_path="scripts/v3_reason_extract.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
        progress_counter="with_reasons",
        ratio_verifiable=False,  # v3 는 row 단위, pending 추정 부정확
    ),
    PipelineStep(
        name="tier1_embedder",
        script_path="scripts/tier1_embedder.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
        progress_counter="with_embeddings",
        ratio_verifiable=True,  # 책 단위 정확
    ),
    PipelineStep(
        name="build_index",
        script_path="scripts/build_index.py",
        supports_limit=False,
        supports_dry_run=False,
        limit_flag=None,
        cwd="recommendation-server",
        progress_counter=None,  # 파일 산출물, DB 카운터 없음
    ),
]


def get_step_by_name(name: str) -> Optional[PipelineStep]:
    for s in STEPS:
        if s.name == name:
            return s
    return None


def build_command(step: PipelineStep, limit: Optional[int], dry_run: bool) -> List[str]:
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
