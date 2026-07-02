"""curate_theme_quality — 미정제 판별 + LLM 응답 검증 (순수 로직).

generate_curation_themes 의 insert-only 계약도 함께 고정한다
(upsert 가 리라이트를 리셋하고 kill 을 부활시키던 함정 회귀 방지).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from curate_theme_quality import is_unrefined, validate_results


class TestIsUnrefined:
    def test_template_desc_is_unrefined(self):
        assert is_unrefined({"target_keyword": "기묘", "description": "기묘 관련 책들"})

    def test_rewritten_desc_is_refined(self):
        assert not is_unrefined({
            "target_keyword": "기묘",
            "description": "기묘한 이야기들이 필요한 밤을 위한 책들",
        })

    def test_non_keyword_theme_skipped(self):
        assert not is_unrefined({"target_keyword": None, "description": "x 관련 책들"})
        assert not is_unrefined({"target_keyword": "", "description": " 관련 책들"})

    def test_other_keyword_template_not_matched(self):
        # description 이 '다른' 키워드의 템플릿이면 이미 손댄 행으로 간주(보수적 skip)
        assert not is_unrefined({"target_keyword": "우주", "description": "기묘 관련 책들"})


class TestValidateResults:
    def test_keep_and_kill_parsed(self):
        actions, errors = validate_results(
            ["기묘", "마음에"],
            {"results": [
                {"keyword": "기묘", "verdict": "keep",
                 "title": "기묘한 이야기가 필요한 밤", "description": "낯설고 이상한 이야기들"},
                {"keyword": "마음에", "verdict": "kill"},
            ]},
        )
        assert errors == []
        assert actions["기묘"]["verdict"] == "keep"
        assert actions["마음에"] == {"verdict": "kill"}

    def test_keep_without_title_rejected(self):
        actions, errors = validate_results(
            ["우주"],
            {"results": [{"keyword": "우주", "verdict": "keep", "title": "", "description": "d"}]},
        )
        assert "우주" not in actions
        assert errors

    def test_title_length_bounds(self):
        long_title = "이" * 30
        actions, _ = validate_results(
            ["우주"],
            {"results": [{"keyword": "우주", "verdict": "keep",
                          "title": long_title, "description": "d"}]},
        )
        assert "우주" not in actions

    def test_unknown_keyword_ignored_missing_reported(self):
        actions, errors = validate_results(
            ["요리"],
            {"results": [{"keyword": "환각키워드", "verdict": "kill"}]},
        )
        assert actions == {}
        assert any("요리" in e for e in errors)  # 응답 누락 보고 → 다음 실행 재시도

    def test_malformed_response(self):
        actions, errors = validate_results(["a"], {"nope": 1})
        assert actions == {} and errors


class TestGeneratorInsertOnly:
    """generate_curation_themes — 기존 theme_key 미접촉(insert-only) 계약."""

    class _Q:
        def __init__(self, sb, table):
            self.sb, self.table = sb, table

        def select(self, *a, **k):
            return self

        def not_(self):  # pragma: no cover
            return self

        def range(self, s, e):
            self._r = (s, e)
            return self

        def insert(self, row):
            self.sb.inserts.append((self.table, row))
            return self

        def execute(self):
            class R:
                data = []
            r = R()
            if self.table == "curation_themes" and not self.sb._keys_served:
                r.data = [{"theme_key": k} for k in self.sb.existing]
                self.sb._keys_served = True
            return r

        def __getattr__(self, name):
            return lambda *a, **k: self

    class _SB:
        def __init__(self, existing):
            self.existing = existing
            self.inserts = []
            self._keys_served = False

        def table(self, name):
            return TestGeneratorInsertOnly._Q(self, name)

    def test_existing_key_skipped_new_inserted(self):
        import generate_curation_themes as g
        sb = self._SB(existing={"keyword|기존"})
        existing = g.existing_theme_keys(sb)
        assert existing == {"keyword|기존"}

        # 직접 insert 헬퍼 계약: upsert 아님(insert) — 리라이트/kill 보존의 근거
        g._insert_theme(sb, theme_key="keyword|신규", theme_type="keyword",
                        title="신규", description="신규 관련 책들",
                        parameters={}, personalization="general")
        assert [t for t, _ in sb.inserts] == ["curation_themes"]
        assert not hasattr(g, "_upsert_theme"), "upsert 경로가 부활하면 안 됨"
