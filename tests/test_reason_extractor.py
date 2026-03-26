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
