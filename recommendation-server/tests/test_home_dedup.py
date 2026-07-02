"""홈 섹션 간 책 중복 제거 — 실측 리포트(한강 컬렉션 직후 화제의 책에 같은 책 2권) 회귀.

+ 대표 저자 정규화 3층 동기(DB 함수·생성 스크립트·앱 util) 중 Python 층 고정.
"""
import os
import sys

import numpy as np

from api.home import assemble_sections_for_user
from engine.index import VectorIndex

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from generate_curation_themes import normalize_primary_author


class TestNormalizePrimaryAuthor:
    def test_role_parens(self):
        assert normalize_primary_author("이해 (지은이)") == "이해"
        assert normalize_primary_author(
            "애거서 크리스티 (지은이), 공경희 (옮긴이)") == "애거서 크리스티"

    def test_trailing_role_word(self):
        assert normalize_primary_author("요한 하리 지음") == "요한 하리"

    def test_clean_passthrough(self):
        assert normalize_primary_author("한강") == "한강"

    def test_multi_author_primary(self):
        assert normalize_primary_author(
            "무적핑크, 핑크잼 (지은이), 와이랩(YLAB) (기획)") == "무적핑크"

    def test_empty(self):
        assert normalize_primary_author("") is None
        assert normalize_primary_author("  ") is None


def _meta(bids):
    return {b: {"title": f"t{b}", "author": f"a{b}", "cover_url": None} for b in bids}


class TestCrossSectionDedup:
    def _assemble(self, **kw):
        defaults = dict(
            tier=2, stage=0, total_likes=10,
            user_books=[], top_authors=[], top_l1s=[],
            recent_curation_ids=set(),
            index=VectorIndex(dim=4),
            recommend_scored=None,
        )
        defaults.update(kw)
        return assemble_sections_for_user(**defaults)

    def test_trending_excludes_books_shown_in_curation(self):
        """큐레이션에 나온 책이 바로 아래 trending 에 반복되지 않는다(실측 케이스)."""
        overlap = [f"b{i}" for i in range(10)]
        extra = [f"x{i}" for i in range(15)]
        themes = [{"id": 1, "title": "한강 컬렉션", "personalization": "general",
                   "priority": 1.0, "click_rate": 0.0, "shown_count": 0}]
        sections = self._assemble(
            active_themes=themes,
            curation_cache_by_id={1: overlap},
            fallback_books=[{"book_id": b} for b in overlap + extra],
            books_meta=_meta(overlap + extra),
        )
        by_type = {}
        for s in sections:
            by_type.setdefault(s["type"], []).append(
                {b["book_id"] for b in s["books"]})
        cur_sets = by_type.get("curation", [])
        trend = by_type["trending"][0]
        assert trend, "trending 은 다음 후보로 채워져야 함"
        for cs in cur_sets:
            assert not (cs & trend), f"섹션 간 중복: {cs & trend}"
        # trending 은 중복을 제외하고도 후보(30)에서 10개를 채운다
        assert len(trend) == 10

    def test_personal_recommend_has_priority(self):
        """personal_recommend(최우선)에 나온 책은 trending 에서 제외."""
        rec = [(f"r{i}", 1.0 - i * 0.01) for i in range(10)]
        rec_bids = [b for b, _ in rec]
        extra = [f"x{i}" for i in range(15)]
        sections = self._assemble(
            recommend_scored=rec,
            active_themes=[],
            curation_cache_by_id={},
            fallback_books=[{"book_id": b} for b in rec_bids + extra],
            books_meta=_meta(rec_bids + extra),
        )
        pr = next(s for s in sections if s["type"] == "personal_recommend")
        trend = next(s for s in sections if s["type"] == "trending")
        pr_set = {b["book_id"] for b in pr["books"]}
        trend_set = {b["book_id"] for b in trend["books"]}
        assert pr_set and not (pr_set & trend_set)
