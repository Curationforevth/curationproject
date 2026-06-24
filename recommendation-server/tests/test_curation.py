from engine.curation import (
    filter_by_personalization,
    weighted_sample_one,
    apply_recent_discount,
)


def test_filter_by_personalization_general_always_passes():
    themes = [{"id": 1, "personalization": "general"}]
    result = filter_by_personalization(themes, tier=0, top_authors=[], top_l1s=[])
    assert len(result) == 1


def test_filter_by_personalization_tier1_plus_blocks_tier0():
    themes = [{"id": 1, "personalization": "tier1+"}]
    assert filter_by_personalization(themes, tier=0, top_authors=[], top_l1s=[]) == []
    assert len(filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=[])) == 1


def test_filter_by_personalization_by_author_requires_match():
    themes = [{"id": 1, "personalization": "by_author", "target_author": "무라카미"}]
    assert filter_by_personalization(themes, tier=2, top_authors=["김영하"], top_l1s=[]) == []
    assert len(filter_by_personalization(themes, tier=2, top_authors=["무라카미"], top_l1s=[])) == 1


def test_filter_by_personalization_by_l1_requires_match():
    themes = [{"id": 1, "personalization": "by_l1", "target_l1": "문학"}]
    assert filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=["과학"]) == []
    assert len(filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=["문학"])) == 1


def test_apply_recent_discount_removes_recent_shown():
    themes = [{"id": 1}, {"id": 2}, {"id": 3}]
    recent_ids = {2}
    result = apply_recent_discount(themes, recent_ids)
    assert [t["id"] for t in result] == [1, 3]


def test_weighted_sample_one_respects_priority():
    # priority 가 압도적으로 높은 항목이 대부분 선택되는지
    import random
    random.seed(42)
    themes = [
        {"id": 1, "priority": 0.01, "click_rate": 0.01, "shown_count": 100},
        {"id": 2, "priority": 100.0, "click_rate": 0.01, "shown_count": 100},
    ]
    counts = {1: 0, 2: 0}
    for _ in range(100):
        picked = weighted_sample_one(themes)
        counts[picked["id"]] += 1
    assert counts[2] > 80


def test_weighted_sample_one_empty_returns_none():
    assert weighted_sample_one([]) is None
