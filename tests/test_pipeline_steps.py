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
