"""Reason Extractor 유닛 테스트 — 순수 함수만"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from reason_extractor import build_extraction_prompt, parse_reasons, build_feedback_prompt, filter_generic_reasons


def test_build_extraction_prompt_includes_title():
    prompt = build_extraction_prompt(
        title="해리 포터와 마법사의 돌",
        genre="소설/시/희곡>판타지",
        description="마법 학교 이야기",
        library_keywords=["해리포터", "마법"],
    )
    assert "해리 포터와 마법사의 돌" in prompt
    assert "판타지" in prompt
    assert "마법 학교 이야기" in prompt


def test_build_extraction_prompt_handles_empty_fields():
    prompt = build_extraction_prompt(
        title="테스트 책",
        genre="",
        description="",
        library_keywords=None,
    )
    assert "테스트 책" in prompt


def test_parse_reasons_valid():
    raw = {"reasons": ["이유 하나", "이유 둘", "이유 셋"]}
    result = parse_reasons(raw)
    assert result == ["이유 하나", "이유 둘", "이유 셋"]


def test_parse_reasons_filters_empty():
    raw = {"reasons": ["이유 하나", "", "  ", "이유 둘"]}
    result = parse_reasons(raw)
    assert result == ["이유 하나", "이유 둘"]


def test_parse_reasons_invalid_format():
    raw = {"error": "something"}
    result = parse_reasons(raw)
    assert result == []


def test_filter_generic_reasons():
    reasons = [
        "호그와트의 디테일한 마법 학교 생활",
        "재밌다",
        "감동적이다",
        "마법사의 돌을 둘러싼 미스터리와 반전",
        "좋은 책",
        "읽어볼 만하다",
    ]
    filtered = filter_generic_reasons(reasons)
    assert "호그와트의 디테일한 마법 학교 생활" in filtered
    assert "마법사의 돌을 둘러싼 미스터리와 반전" in filtered
    assert "재밌다" not in filtered
    assert "좋은 책" not in filtered


def test_build_feedback_prompt():
    prompt = build_feedback_prompt("세계관이 새롭고 디테일하고 몰입이 되었어")
    assert "세계관이 새롭고" in prompt


def test_build_feedback_prompt_short_input():
    prompt = build_feedback_prompt("좋았어요")
    assert "좋았어요" in prompt


# ----- main() exit code 계약 -----
# Step 1+2: errors_books / errors_rows 분리 + ratio-based exit code

from unittest.mock import patch, MagicMock


def _run_main_with_stats(stats_dict):
    """main() 을 호출하되 ReasonExtractor 를 stub 으로 교체."""
    import sys as _sys
    argv_backup = _sys.argv
    _sys.argv = ["reason_extractor.py"]
    try:
        with patch("reason_extractor.create_client"), \
             patch("reason_extractor.ReasonExtractor") as FakeExtractor:
            instance = MagicMock()
            instance.stats = stats_dict
            instance.run = MagicMock()
            FakeExtractor.return_value = instance
            from reason_extractor import main
            return main()
    finally:
        _sys.argv = argv_backup


def test_main_exit_zero_on_clean_run():
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 1300,
        "skipped": 5, "errors_books": 0, "errors_rows": 0, "deleted": 0,
    }) == 0


def test_main_exit_one_on_errors_books():
    """LLM 또는 임베딩으로 책이 통째로 손실 → 항상 fail."""
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 1300,
        "skipped": 0, "errors_books": 3, "errors_rows": 0, "deleted": 0,
    }) == 1


def test_main_exit_zero_on_low_row_failure_ratio():
    """row 실패율 5% (50 / (950+50)) — 10% 임계 이하 → exit 0."""
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 950,
        "skipped": 0, "errors_books": 0, "errors_rows": 50, "deleted": 0,
    }) == 0


def test_main_exit_one_on_high_row_failure_ratio():
    """row 실패율 20% (200 / (800+200)) → exit 1."""
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 800,
        "skipped": 0, "errors_books": 0, "errors_rows": 200, "deleted": 0,
    }) == 1


def test_main_exit_one_when_books_fail_even_if_rows_ok():
    """errors_books 가 우선 — rows 비율과 관계없이 fail."""
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 1300,
        "skipped": 0, "errors_books": 1, "errors_rows": 0, "deleted": 0,
    }) == 1


def test_main_exit_zero_at_exactly_10_percent_boundary():
    """row 실패율 정확히 10% — 정책상 exit 0 (`> 0.10` 사용)."""
    # 100 / (900 + 100) = 10.0%
    assert _run_main_with_stats({
        "processed": 100, "reasons_created": 900,
        "skipped": 0, "errors_books": 0, "errors_rows": 100, "deleted": 0,
    }) == 0
