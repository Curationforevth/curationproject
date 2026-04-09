"""taste_recomputer 하드닝 테스트.

목적: hard import + statement_timeout 판별 + run() exit code +
       _refresh_confidence 가 silent pass 하지 않음.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch


class FakeAPIError(Exception):
    def __init__(self, code, message="err"):
        self.code = code
        super().__init__(message)


def test_hard_import_no_silent_fallback():
    import taste_recomputer
    from lib.retry import with_retry as real_retry
    assert taste_recomputer.with_retry is real_retry
    from lib.batch_fallback import save_with_size_fallback as real_helper
    assert taste_recomputer.save_with_size_fallback is real_helper


def test_is_statement_timeout_true():
    from taste_recomputer import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("57014")) is True
    assert _is_statement_timeout(FakeAPIError("23505")) is False
    assert _is_statement_timeout(ValueError("x")) is False


def test_feedback_weight_bad_returns_zero():
    from taste_recomputer import feedback_weight
    assert feedback_weight({"rating": "bad"}) == 0


def test_feedback_weight_review_higher_than_tag():
    from taste_recomputer import feedback_weight
    long_review = {"review_text": "a" * 100, "rating": "good"}
    tag_only = {"emotion_tags": ["감동"], "rating": "good"}
    assert feedback_weight(long_review) > feedback_weight(tag_only)


def test_should_upgrade_to_kmeans_threshold():
    from taste_recomputer import should_upgrade_to_kmeans, KMEANS_MIN_BOOKS
    assert should_upgrade_to_kmeans(KMEANS_MIN_BOOKS, "weighted_avg") is True
    assert should_upgrade_to_kmeans(KMEANS_MIN_BOOKS - 1, "weighted_avg") is False


def test_run_returns_zero_when_no_users():
    import taste_recomputer
    with patch.object(taste_recomputer, "create_client", return_value=MagicMock()):
        rc = taste_recomputer.TasteRecomputer(dry_run=True)
        with patch.object(rc, "fetch_users_with_books", return_value=[]):
            ret = rc.run()
    assert ret == 0


def test_run_returns_one_on_user_error():
    """process_user 가 던진 예외 → errors 카운트 + exit 1."""
    import taste_recomputer
    with patch.object(taste_recomputer, "create_client", return_value=MagicMock()):
        rc = taste_recomputer.TasteRecomputer(dry_run=True)
        with patch.object(rc, "fetch_users_with_books", return_value=["uuid1234"]):
            with patch.object(rc, "process_user", side_effect=RuntimeError("boom")):
                ret = rc.run()
    assert ret == 1
    assert rc.stats["errors"] == 1


def test_refresh_confidence_failure_counted_not_swallowed():
    """confidence RPC 실패 → silent pass 하지 않고 카운트 + exit 1 유발."""
    import taste_recomputer
    with patch.object(taste_recomputer, "create_client", return_value=MagicMock()):
        rc = taste_recomputer.TasteRecomputer(dry_run=False)
        # rpc 가 실패하도록 mock
        rc.sb.rpc.side_effect = RuntimeError("rpc down")
        rc._refresh_confidence("user-id-1234")
    assert rc.stats["confidence_failed"] == 1


def test_refresh_confidence_skipped_in_dry_run():
    import taste_recomputer
    with patch.object(taste_recomputer, "create_client", return_value=MagicMock()):
        rc = taste_recomputer.TasteRecomputer(dry_run=True)
        rc.sb.rpc.side_effect = RuntimeError("should not be called")
        rc._refresh_confidence("user-id")
    assert rc.stats["confidence_failed"] == 0
