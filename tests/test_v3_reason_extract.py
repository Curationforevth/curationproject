"""v3_reason_extract 최소 단위 테스트 (A4)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_source_tag_is_v3_context_rich():
    import v3_reason_extract
    assert v3_reason_extract.SOURCE_TAG == "v3_context_rich"


def test_build_v3_prompt_contains_rules():
    from v3_reason_extract import build_v3_prompt
    prompt = build_v3_prompt("제목", "소설", "본문 설명")
    # v3 프롬프트는 reason 생성 규칙 + 본문을 포함해야 한다
    assert "이유" in prompt
    assert "본문 설명" in prompt


def test_filter_v3_reasons_drops_short_and_title():
    from v3_reason_extract import filter_v3_reasons
    title = "마음의 평화"
    reasons = [
        "짧음",  # 10자 미만 → 탈락
        "마음의 평화",  # title 자체 → 탈락
        "이것은 15자 이상인 맥락이 있는 이유 하나",  # 통과
    ]
    out = filter_v3_reasons(reasons, title)
    assert "짧음" not in out
    assert "마음의 평화" not in out
    assert any("15자 이상" in r for r in out)
