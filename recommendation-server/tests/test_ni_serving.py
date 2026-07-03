"""서빙 레이어 관심없음(NI) 필터 — 2026-07-03 QA 결함 1·2 회귀 방지.

배경: NI 필터가 personal_recommend(/recommend, /home 393행)에만 있어
curation/trending/similar 표면에 NI 책이 재등장했고, /home 캐시 히트 경로는
신호를 아예 안 읽어 최대 1시간 stale 서빙됐다. 수정 = 캐시 원본 불변 +
서빙 직전 strip_not_interested(전 섹션) + /similar 계열 filter_not_interested.
"""
from __future__ import annotations

from engine.dedup import strip_not_interested, filter_not_interested


def _sections():
    return [
        {"id": "curation_0", "type": "curation", "title": "테마",
         "books": [{"book_id": "a"}, {"book_id": "ni1"}, {"book_id": "b"}]},
        {"id": "trending_1", "type": "trending", "title": "화제",
         "books": [{"book_id": "ni1"}, {"book_id": "ni2"}]},
        {"id": "nav_2", "type": "category_nav", "title": "카테고리"},
    ]


class TestStripNotInterested:
    def test_ni_removed_from_every_section(self):
        out = strip_not_interested(_sections(), {"ni1", "ni2"})
        curation = out[0]
        assert [b["book_id"] for b in curation["books"]] == ["a", "b"]

    def test_fully_filtered_section_dropped(self):
        out = strip_not_interested(_sections(), {"ni1", "ni2"})
        assert all(s["id"] != "trending_1" for s in out)

    def test_non_book_section_kept(self):
        out = strip_not_interested(_sections(), {"ni1", "ni2"})
        assert any(s["type"] == "category_nav" for s in out)

    def test_empty_ni_is_identity(self):
        sections = _sections()
        assert strip_not_interested(sections, set()) is sections

    def test_original_sections_not_mutated(self):
        sections = _sections()
        strip_not_interested(sections, {"ni1"})
        assert len(sections[0]["books"]) == 3  # 캐시 원본 불변


class TestFilterNotInterested:
    def test_scored_candidates_filtered(self):
        raw = [("a", 0.9), ("ni1", 0.8), ("b", 0.7)]
        assert filter_not_interested(raw, {"ni1"}) == [("a", 0.9), ("b", 0.7)]

    def test_empty_ni_is_identity(self):
        raw = [("a", 0.9)]
        assert filter_not_interested(raw, set()) is raw
