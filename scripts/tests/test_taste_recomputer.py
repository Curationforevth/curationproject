"""취향 재계산기 순수 함수 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestFeedbackDepthScore:
    """피드백 깊이 스코어 계산"""

    def test_read_only(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": None, "emotion_tags": None, "review_text": None}
        assert feedback_depth_score(book) == 1

    def test_with_rating(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": None, "review_text": None}
        assert feedback_depth_score(book) == 2

    def test_with_few_tags(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한", "따뜻한"], "review_text": None}
        assert feedback_depth_score(book) == 3

    def test_with_many_tags(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한", "따뜻한", "몰입"], "review_text": None}
        assert feedback_depth_score(book) == 4

    def test_with_review(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": "a" * 50}
        assert feedback_depth_score(book) == 5

    def test_short_review_not_counted(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": "짧은 리뷰"}
        assert feedback_depth_score(book) == 3


class TestShouldUpgradeToKmeans:
    """클러스터링 전환 판단"""

    def test_too_few_books(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(5, 'weighted_avg') == False

    def test_enough_books_weighted_avg(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(12, 'weighted_avg') == True

    def test_already_kmeans(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(12, 'kmeans') == True

    def test_boundary(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(10, 'weighted_avg') == True


class TestFeedbackWeight:
    """가중 평균용 weight 계산"""

    def test_read_only(self):
        from taste_recomputer import feedback_weight
        book = {"rating": None, "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 1.0

    def test_with_rating_good(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 1.5

    def test_with_tags(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 2.0

    def test_with_review(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": ["잔잔한"],
                "review_text": "a" * 50, "is_onboarding_favorite": False}
        assert feedback_weight(book) == 3.0

    def test_favorite_bonus(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": True}
        assert feedback_weight(book) == 1.5 * 1.2

    def test_bad_rating_excluded(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "bad", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 0
