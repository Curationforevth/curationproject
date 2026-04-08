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
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.side_effect = [
            StepResult("yes24_scraper", True, 0, None),
            StepResult("v3_vectors", False, 2, "boom"),
        ]
        results = run_pipeline(limit=None, dry_run=False)
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False
    assert mock_step.call_count == 2


def test_run_pipeline_runs_all_steps_on_success():
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.return_value = StepResult("s", True, 0, None)
        results = run_pipeline(limit=None, dry_run=False)
    assert len(results) == len(STEPS)
    assert all(r.success for r in results)


def test_run_pipeline_skips_before_from_step():
    with patch("scripts.pipeline_orchestrator.run_step") as mock_step:
        mock_step.return_value = StepResult("s", True, 0, None)
        results = run_pipeline(limit=None, dry_run=False, from_step="v3_vectors")
    executed_names = [c.args[0].name for c in mock_step.call_args_list]
    assert "yes24_scraper" not in executed_names
    assert executed_names[0] == "v3_vectors"
    assert len(results) == len(STEPS) - 1


def test_run_pipeline_only_single_step():
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


from scripts import pipeline_orchestrator
from scripts.pipeline_orchestrator import collect_status, print_status


def test_collect_status_aggregates_counts(monkeypatch):
    """collect_status delegates to internal _count_* helpers; we stub them."""
    canned = {
        ("not_null", "books", "loan_count"): 1019,
        ("missing", "books", "loan_count", "rich_description"): 745,
        ("not_null", "books", "rich_description"): 2678,
        ("total", "book_v3_vectors"): 2651,
        ("total", "book_embeddings"): 8564,
    }

    def fake_count_not_null(sb, table, col):
        return canned[("not_null", table, col)]

    def fake_count_missing(sb, table, have_col, missing_col):
        return canned[("missing", table, have_col, missing_col)]

    def fake_count_total(sb, table):
        return canned[("total", table)]

    monkeypatch.setattr(pipeline_orchestrator, "_count_not_null", fake_count_not_null)
    monkeypatch.setattr(pipeline_orchestrator, "_count_missing", fake_count_missing)
    monkeypatch.setattr(pipeline_orchestrator, "_count_total", fake_count_total)

    status = collect_status(sb=None)
    assert status["with_loan_count"] == 1019
    assert status["missing_rich_description"] == 745
    assert status["with_rich_description"] == 2678
    assert status["with_v3_vectors"] == 2651
    assert status["with_embeddings"] == 8564


def test_print_status_does_not_crash(capsys):
    """Smoke test the printer."""
    status = {
        "with_loan_count": 1,
        "missing_rich_description": 2,
        "with_rich_description": 3,
        "with_v3_vectors": 4,
        "with_embeddings": 5,
    }
    print_status(status)
    out = capsys.readouterr().out
    assert "Pipeline Status" in out
    assert "1019" not in out  # sanity: just to confirm distinct keys printed
    assert "1" in out and "2" in out and "3" in out
