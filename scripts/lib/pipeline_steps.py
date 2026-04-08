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


STEPS: List[PipelineStep] = [
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
