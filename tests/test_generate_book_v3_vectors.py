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


class _FakeQ:
    def __init__(self, rows, rec):
        self._rows, self._rec = rows, rec

    def select(self, cols):
        self._rec["cols"] = cols
        return self

    @property
    def not_(self):
        self._rec["not_used"] = True
        return self

    def is_(self, *a):
        return self

    def range(self, a, b):
        self._rec["range"] = (a, b)
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeSB:
    def __init__(self, rows, rec):
        self._rows, self._rec = rows, rec

    def table(self, t):
        return _FakeQ(self._rows, self._rec)


def test_fetch_target_books_no_rich_filter_includes_author():
    from generate_book_v3_vectors import fetch_target_books
    rec = {}
    sb = _FakeSB(rows=[], rec=rec)
    fetch_target_books(sb, 10)
    assert "author" in rec["cols"], "minimal 폴백용 author SELECT 포함"
    assert "not_used" not in rec, "rich_description IS NOT NULL 필터 제거됨"


def test_fetch_target_books_excludes_existing_before_limit():
    from generate_book_v3_vectors import fetch_target_books
    rec = {}
    rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    sb = _FakeSB(rows=rows, rec=rec)
    out = fetch_target_books(sb, 10, existing_ids={"a", "b"})
    assert [r["id"] for r in out] == ["c"], "이미 임베딩된 책은 제외(대표성)"
