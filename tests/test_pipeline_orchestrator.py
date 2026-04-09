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
        ("total", "book_love_reasons"): 37911,
        ("total", "book_embeddings"): 8564,
    }

    def fake_count_not_null(sb, table, col):
        return canned[("not_null", table, col)]

    def fake_count_missing(sb, table, have_col, missing_col):
        return canned[("missing", table, have_col, missing_col)]

    def fake_count_total(sb, table, pk="id"):
        return canned[("total", table)]

    monkeypatch.setattr(pipeline_orchestrator, "_count_not_null", fake_count_not_null)
    monkeypatch.setattr(pipeline_orchestrator, "_count_missing", fake_count_missing)
    monkeypatch.setattr(pipeline_orchestrator, "_count_total", fake_count_total)

    status = collect_status(sb=None)
    assert status["with_loan_count"] == 1019
    assert status["missing_rich_description"] == 745
    assert status["with_rich_description"] == 2678
    assert status["with_v3_vectors"] == 2651
    assert status["with_reasons"] == 37911
    assert status["with_embeddings"] == 8564


def _mk_step(name, counter):
    """테스트용 PipelineStep (build_command 가 호출되지만 subprocess 는 mock)."""
    return PipelineStep(
        name=name,
        script_path=f"scripts/{name}.py",
        supports_limit=True,
        supports_dry_run=True,
        limit_flag="--limit",
        progress_counter=counter,
    )


def test_run_step_progress_verification_passes(monkeypatch):
    """pre=100, post=145, limit=50 → delta=45, ratio=0.9 = 경계, 성공."""
    step = _mk_step("fake", "with_rich_description")
    sb = MagicMock()

    status_seq = [
        {"with_rich_description": 100, "missing_rich_description": 200},  # pre
        {"with_rich_description": 145, "missing_rich_description": 155},  # post
    ]

    def fake_collect(_sb):
        return status_seq.pop(0)

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=50, dry_run=False, sb=sb)

    assert r.success is True
    assert r.progress_delta == 45
    assert r.progress_expected == 50
    assert r.progress_warning is None


def test_run_step_progress_verification_fails_on_zero_delta(monkeypatch):
    """pre=100, post=100 → delta=0, expected=50 → 실패."""
    step = _mk_step("fake", "with_rich_description")
    sb = MagicMock()

    status_seq = [
        {"with_rich_description": 100, "missing_rich_description": 200},
        {"with_rich_description": 100, "missing_rich_description": 200},
    ]

    def fake_collect(_sb):
        return status_seq.pop(0)

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)  # subprocess success!
        r = pipeline_orchestrator.run_step(step, limit=50, dry_run=False, sb=sb)

    # subprocess 는 성공했지만 DB 검증으로 실패 처리
    assert r.success is False
    assert r.progress_delta == 0
    assert r.progress_warning is not None
    assert "기대치 미달" in r.progress_warning or "진전 0" in r.progress_warning


def test_run_step_progress_verification_fails_on_below_threshold(monkeypatch):
    """pre=100, post=120, limit=100 → delta=20/100=20% < 90%, 실패."""
    step = _mk_step("fake", "with_rich_description")
    sb = MagicMock()

    status_seq = [
        {"with_rich_description": 100, "missing_rich_description": 500},
        {"with_rich_description": 120, "missing_rich_description": 480},
    ]

    def fake_collect(_sb):
        return status_seq.pop(0)

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=100, dry_run=False, sb=sb)

    assert r.success is False
    assert r.progress_delta == 20
    assert "미달" in r.progress_warning


def test_run_step_skips_verification_when_sb_none():
    """sb=None → DB 검증 안 하고 exit code 만 본다 (기존 동작 보존)."""
    step = _mk_step("fake", "with_rich_description")
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=50, dry_run=False, sb=None)
    assert r.success is True
    assert r.progress_delta is None
    assert r.progress_warning is None


def test_run_step_skips_verification_on_dry_run(monkeypatch):
    """dry_run=True → DB 검증 스킵 (DB 에 변화가 없으므로 false-positive 방지)."""
    step = _mk_step("fake", "with_rich_description")
    sb = MagicMock()
    monkeypatch.setattr(pipeline_orchestrator, "collect_status",
                        lambda _sb: {"with_rich_description": 100,
                                     "missing_rich_description": 200})
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=50, dry_run=True, sb=sb)
    assert r.success is True
    assert r.progress_delta is None


def test_run_step_skips_verification_when_no_progress_counter(monkeypatch):
    """build_index 같이 progress_counter=None 인 step 은 DB 검증 건너뜀."""
    step = PipelineStep(
        name="build_index",
        script_path="scripts/build_index.py",
        supports_limit=False,
        supports_dry_run=False,
        limit_flag=None,
        progress_counter=None,
    )
    sb = MagicMock()
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=None, dry_run=False, sb=sb)
    assert r.success is True
    assert r.progress_delta is None


def test_print_status_does_not_crash(capsys):
    """Smoke test the printer."""
    status = {
        "with_loan_count": 1,
        "missing_rich_description": 2,
        "with_rich_description": 3,
        "with_v3_vectors": 4,
        "with_reasons": 7,
        "with_embeddings": 5,
    }
    print_status(status)
    out = capsys.readouterr().out
    assert "Pipeline Status" in out
    assert "book_love_reasons" in out
    # 각 카운트가 출력에 포함되는지
    for v in status.values():
        assert str(v) in out
