import json
import os

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "source_tier_cases.json")


def _cases():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_build_desc_source_matches_fixture():
    """배치 build_desc_source 가 공유 픽스처의 (text, tier) 를 그대로 산출."""
    from generate_book_v3_vectors import build_desc_source
    for c in _cases():
        text, tier = build_desc_source(c["row"])
        assert tier == c["tier"], f"{c['name']}: tier {tier} != {c['tier']}"
        if c.get("text") is not None:
            assert text == c["text"], f"{c['name']}: text mismatch"
        if c["tier"] == "rich":
            assert text is not None and len(text) >= 200


def test_build_desc_source_only_emits_known_tiers():
    """drift 가드: build_desc_source 는 정본 tier 문자열만 산출."""
    from generate_book_v3_vectors import build_desc_source
    allowed = {"rich", "kakao_desc", "minimal", None}
    for c in _cases():
        _, tier = build_desc_source(c["row"])
        assert tier in allowed
