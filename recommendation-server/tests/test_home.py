from unittest.mock import MagicMock

from api.home import assemble_sections_for_user


def test_assemble_sections_tier_0_returns_trending_and_curations():
    """Tier 0: [trending, curation, curation, category_nav]"""
    fake_books_meta = {"b1": {"title": "T1", "author": "A", "cover_url": None}}
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
