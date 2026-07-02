from unittest.mock import MagicMock

from api.home import (assemble_sections_for_user, _similar_books_from_seed,
                     _drop_empty_sections, _safe)


def test_similar_books_from_seed_uses_correct_signature(small_index):
    # 회귀: similar_by_desc(book_id, limit=10) — 과거 top_n= 오인자로 TypeError →
    # except 가 삼켜 Tier 1 유저 similar 섹션이 통째로 비던 버그. 실제 VectorIndex 로
    # 호출해 시그니처가 맞아야 결과가 채워진다. (MagicMock 으로는 오인자도 통과해
    # 회귀를 못 잡으므로 반드시 실제 인덱스로 검증.)
    small_index.build_desc_matrix()
    meta = {bid: {"title": bid, "author": "a", "cover_url": "http://cover"}
            for bid in small_index.book_ids}
    out = _similar_books_from_seed(small_index, meta, "novel1", limit=3)
    assert out, "similar 결과가 비어있음 — 시그니처 불일치(top_n=) 회귀"
    assert "novel1" not in [b["book_id"] for b in out], "seed 자신은 제외돼야"


def test_assemble_sections_tier_0_returns_trending_and_curations():
    """Tier 0: [trending, curation, curation, category_nav]"""
    fake_books_meta = {"b1": {"title": "T1", "author": "A", "cover_url": "http://cover"}}
    fake_fallback = [{"book_id": "b1"}]  # fallback_curation row
    fake_themes = [
        {"id": 10, "theme_type": "genre_combo", "title": "문학", "personalization": "general",
         "priority": 1.0, "click_rate": 0.0},
    ]
    fake_cache_rows = {10: ["b1"]}

    sections = assemble_sections_for_user(
        tier=0, stage=0, total_likes=0,
        user_books=[],
        top_authors=[], top_l1s=[],
        recent_curation_ids=set(),
        fallback_books=fake_fallback,
        active_themes=fake_themes,
        curation_cache_by_id=fake_cache_rows,
        books_meta=fake_books_meta,
        index=None,
    )
    types = [s["type"] for s in sections]
    assert types[0] == "trending"
    assert types[-1] == "category_nav"
    assert len(sections) == 4


def test_assemble_sections_tier_2_has_personal_recommend_first():
    sections = assemble_sections_for_user(
        tier=2, stage=0, total_likes=10,
        user_books=[{"book_id": "b1", "rating": "good"}],
        top_authors=["A"], top_l1s=["문학"],
        recent_curation_ids=set(),
        fallback_books=[],
        active_themes=[],
        curation_cache_by_id={},
        books_meta={},
        index=None,
        recommend_scored=[],  # Tier 2 에서 recommend_core 결과 주입
    )
    types = [s["type"] for s in sections]
    assert types[0] == "personal_recommend"
    assert "category_nav" not in types


# --- 견고화 (앱이 /home 을 직접 렌더하므로) ---

class TestDropEmptySections:
    def test_drops_empty_book_sections(self):
        secs = [
            {"type": "trending", "title": "화제의 책", "books": [{"book_id": "a"}]},
            {"type": "curation", "title": "", "books": []},               # 빈 큐레이션 → 제거
            {"type": "personal_recommend", "title": "추천", "books": []},  # 빈 추천 → 제거
        ]
        out = _drop_empty_sections(secs)
        assert [s["type"] for s in out] == ["trending"]

    def test_keeps_category_nav_even_when_empty(self):
        secs = [
            {"type": "category_nav", "title": "", "books": []},
            {"type": "curation", "title": "공부", "books": [{"book_id": "x"}]},
        ]
        out = _drop_empty_sections(secs)
        assert [s["type"] for s in out] == ["category_nav", "curation"]

    def test_all_populated_passthrough(self):
        secs = [
            {"type": "personal_recommend", "books": [{"book_id": "a"}]},
            {"type": "curation", "books": [{"book_id": "b"}]},
        ]
        assert len(_drop_empty_sections(secs)) == 2


class TestSafe:
    def test_returns_value_on_success(self):
        assert _safe(lambda: 42, default=0) == 42

    def test_returns_default_on_exception(self):
        def boom():
            raise RuntimeError("db down")
        assert _safe(boom, default=[]) == []
