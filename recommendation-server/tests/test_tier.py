from engine.tier import user_tier_from_likes, cta_for_tier, korean_particle, sections_for_tier

def test_user_tier_boundaries():
    assert user_tier_from_likes(0) == 0
    assert user_tier_from_likes(2) == 0
    assert user_tier_from_likes(3) == 1
    assert user_tier_from_likes(5) == 1
    assert user_tier_from_likes(6) == 2
    assert user_tier_from_likes(100) == 2

def test_cta_for_tier_0_counts_remaining():
    assert cta_for_tier(0, total_likes=0) == "좋아요 3권 더 누르면 비슷한 책 추천이 시작돼요"
    assert cta_for_tier(0, total_likes=2) == "좋아요 1권 더 누르면 비슷한 책 추천이 시작돼요"

def test_cta_for_tier_1_counts_remaining():
    assert cta_for_tier(1, total_likes=3) == "좋아요 3권 더 평가하면 취향 추천이 시작돼요"
    assert cta_for_tier(1, total_likes=5) == "좋아요 1권 더 평가하면 취향 추천이 시작돼요"

def test_cta_for_tier_2_none():
    assert cta_for_tier(2, total_likes=6) is None

def test_korean_particle_with_batchim():
    assert korean_particle("책", "과", "와") == "과"  # '책' 받침 있음
    assert korean_particle("나", "과", "와") == "와"  # '나' 받침 없음

def test_korean_particle_non_korean_uses_without():
    assert korean_particle("Book", "과", "와") == "와"

def test_sections_for_tier_0_has_4_sections():
    secs = sections_for_tier(0)
    assert len(secs) == 4
    assert secs[0]["type"] == "trending"
    assert secs[-1]["type"] == "category_nav"

def test_sections_for_tier_1_has_5_sections_with_similar_first():
    secs = sections_for_tier(1)
    assert len(secs) == 5
    assert secs[0]["type"] == "similar"

def test_sections_for_tier_2_has_5_sections_with_personal_recommend_first_no_category_nav():
    secs = sections_for_tier(2)
    assert len(secs) == 5
    assert secs[0]["type"] == "personal_recommend"
    assert all(s["type"] != "category_nav" for s in secs)
