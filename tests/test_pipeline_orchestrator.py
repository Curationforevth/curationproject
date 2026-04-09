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


# ----- _pending_for_step: step 별 pending 추정 -----

def test_pending_for_yes24_scraper_uses_missing_rich_description():
    from scripts.pipeline_orchestrator import _pending_for_step
    status = {"missing_rich_description": 525}
    assert _pending_for_step("yes24_scraper", status) == 525


def test_pending_for_v3_vectors_is_rich_minus_v3():
    from scripts.pipeline_orchestrator import _pending_for_step
    status = {"with_rich_description": 2898, "with_v3_vectors": 2811}
    assert _pending_for_step("v3_vectors", status) == 87


def test_pending_for_tier1_embedder_is_rich_minus_embeddings():
    from scripts.pipeline_orchestrator import _pending_for_step
    status = {"with_rich_description": 2898, "with_embeddings": 2500}
    assert _pending_for_step("tier1_embedder", status) == 398


def test_pending_for_reason_extractor_uses_v3_minus_reasons_approx():
    from scripts.pipeline_orchestrator import _pending_for_step
    status = {"with_v3_vectors": 2811, "with_reasons": 37911}
    # reasons // 13 ≈ 2916 → max(0, 2811 - 2916) = 0 (이미 다 처리됨)
    assert _pending_for_step("reason_extractor", status) == 0


def test_pending_for_unknown_step_returns_none():
    from scripts.pipeline_orchestrator import _pending_for_step
    assert _pending_for_step("nonexistent", {}) is None


def test_pending_for_v3_vectors_clamps_at_zero():
    """앞 stage 카운트보다 현재 stage 가 더 많으면 (다른 경로로 생성된 경우) 0."""
    from scripts.pipeline_orchestrator import _pending_for_step
    status = {"with_rich_description": 100, "with_v3_vectors": 200}
    assert _pending_for_step("v3_vectors", status) == 0


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
    """pre=100, post=100, pending=200 → delta=0 → silent drop 으로 잡힘."""
    # yes24_scraper 는 RATIO_VERIFY_STEPS 멤버이지만, 0 진전 감지는 멤버십 무관.
    step = _mk_step("yes24_scraper", "with_rich_description")
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
    assert "silent drop" in r.progress_warning or "진전 0" in r.progress_warning


def test_run_step_progress_verification_fails_on_below_threshold(monkeypatch):
    """yes24_scraper, pre=100, post=120, limit=100 → 20/100=20% < 90% → fail."""
    # ratio 검증은 RATIO_VERIFY_STEPS 멤버 한정 → yes24_scraper 사용
    step = _mk_step("yes24_scraper", "with_rich_description")
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


def test_run_step_reason_extractor_skips_ratio_verification(monkeypatch):
    """reason_extractor 는 ratio 검증 비활성 — 부정확한 추정으로 false-positive 방지.
    단 0 진전 감지는 여전히 동작해야 함.
    """
    step = _mk_step("reason_extractor", "with_reasons")
    sb = MagicMock()

    # delta = 100, expected_inaccurate = 1000 → ratio 10% (90% 미달)
    # but reason_extractor 는 RATIO_VERIFY_STEPS 멤버 아니므로 ratio 무시
    status_seq = [
        {
            "with_v3_vectors": 2000,
            "with_reasons": 13000,  # est books = 1000
            "with_rich_description": 2000,
            "with_embeddings": 2000,
            "missing_rich_description": 0,
            "with_loan_count": 100,
        },
        {
            "with_v3_vectors": 2000,
            "with_reasons": 13100,  # +100 row
            "with_rich_description": 2000,
            "with_embeddings": 2000,
            "missing_rich_description": 0,
            "with_loan_count": 100,
        },
    ]

    def fake_collect(_sb):
        return status_seq.pop(0)

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=None, dry_run=False, sb=sb)

    # delta=100, pending est = 2000 - 1000 = 1000 → ratio 10% 인데 검증 비활성
    assert r.success is True
    assert r.progress_delta == 100
    assert r.progress_warning is None


def test_run_step_reason_extractor_still_catches_zero_delta(monkeypatch):
    """reason_extractor 가 ratio 검증 비활성이지만 0 진전은 잡혀야 함."""
    step = _mk_step("reason_extractor", "with_reasons")
    sb = MagicMock()

    # pending est = 1000 인데 delta = 0
    same_status = {
        "with_v3_vectors": 2000,
        "with_reasons": 13000,
        "with_rich_description": 2000,
        "with_embeddings": 2000,
        "missing_rich_description": 0,
        "with_loan_count": 100,
    }

    def fake_collect(_sb):
        return same_status

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=None, dry_run=False, sb=sb)

    assert r.success is False
    assert r.progress_delta == 0
    assert "silent drop" in r.progress_warning


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


def test_run_step_catches_tier1_zero_delta_on_full_backlog(monkeypatch):
    """**핵심 회귀 테스트** — limit 없는 full-backlog 에서 tier1 이 0 진전하면 잡혀야 함.

    어젯밤 사고 재현: rich_description 2898, embeddings 2500 → pending 398.
    tier1 이 내부에서 다 drop 하고 exit 0 → delta 0, expected 398 → 실패 처리.
    """
    step = _mk_step("tier1_embedder", "with_embeddings")
    sb = MagicMock()

    status_seq = [
        {  # pre
            "with_rich_description": 2898,
            "with_v3_vectors": 2811,
            "with_reasons": 37911,
            "with_embeddings": 2500,
            "missing_rich_description": 525,
            "with_loan_count": 1019,
        },
        {  # post — 안 변함 (silent drop)
            "with_rich_description": 2898,
            "with_v3_vectors": 2811,
            "with_reasons": 37911,
            "with_embeddings": 2500,
            "missing_rich_description": 525,
            "with_loan_count": 1019,
        },
    ]

    def fake_collect(_sb):
        return status_seq.pop(0)

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)  # 🎉
        r = pipeline_orchestrator.run_step(
            step, limit=None, dry_run=False, sb=sb  # limit 없음 = full backlog
        )

    assert r.success is False, "full-backlog 에서도 0 진전을 감지해야 한다"
    assert r.progress_delta == 0
    assert r.progress_expected == 398  # pending = 2898 - 2500
    # 0 진전 감지가 ratio 검증보다 먼저 실행되므로 silent drop 메시지
    assert "silent drop" in r.progress_warning or "진전 0" in r.progress_warning


def test_run_step_pending_zero_does_not_false_positive(monkeypatch):
    """처리할 게 없는 full-backlog 는 delta 0 으로 끝나도 실패 아님."""
    step = _mk_step("tier1_embedder", "with_embeddings")
    sb = MagicMock()

    empty_status = {
        "with_rich_description": 100,
        "with_v3_vectors": 100,
        "with_reasons": 1300,
        "with_embeddings": 100,  # 이미 다 처리
        "missing_rich_description": 0,
        "with_loan_count": 100,
    }

    def fake_collect(_sb):
        return empty_status

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(
            step, limit=None, dry_run=False, sb=sb
        )

    assert r.success is True  # 아무것도 할 게 없었으니 OK
    assert r.progress_delta == 0
    assert r.progress_expected == 0
    assert r.progress_warning is None


def test_run_step_pre_snapshot_failure_skips_verification_with_warning(monkeypatch):
    """pre snapshot 이 에러나면 경고만 남기고 subprocess 는 그대로 실행."""
    step = _mk_step("yes24_scraper", "with_rich_description")
    sb = MagicMock()

    def fake_collect(_sb):
        raise RuntimeError("supabase blip")

    monkeypatch.setattr(pipeline_orchestrator, "collect_status", fake_collect)
    with patch("scripts.pipeline_orchestrator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        r = pipeline_orchestrator.run_step(step, limit=50, dry_run=False, sb=sb)

    assert r.success is True  # subprocess 가 OK 였으므로 통과
    assert r.progress_delta is None
    assert r.progress_warning == "pre snapshot 실패로 DB 검증 생략됨"


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
