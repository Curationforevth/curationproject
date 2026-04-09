"""smart_batch_collector 하드닝 테스트.

목적: hard import + save_batch 가 silent drop 안 함 + main() exit code.
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


def _make_collector(dry_run=False):
    """create_client / Aladin / StateManager / DedupChecker mock 하고 collector 생성."""
    import smart_batch_collector
    with patch.object(smart_batch_collector, "create_client", return_value=MagicMock()), \
         patch.object(smart_batch_collector, "AladinClient", return_value=MagicMock()), \
         patch.object(smart_batch_collector, "StateManager", return_value=MagicMock()), \
         patch.object(smart_batch_collector, "DeduplicateChecker", return_value=MagicMock()):
        return smart_batch_collector.SmartBatchCollector(dry_run=dry_run)


def test_hard_import_no_silent_fallback():
    import smart_batch_collector
    from lib.retry import with_retry as real_retry
    assert smart_batch_collector.with_retry is real_retry
    from lib.batch_fallback import save_with_size_fallback as real_helper
    assert smart_batch_collector.save_with_size_fallback is real_helper


def test_is_statement_timeout():
    from smart_batch_collector import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("57014")) is True
    assert _is_statement_timeout(FakeAPIError("23505")) is False


def test_save_batch_dry_run_returns_all_saved():
    collector = _make_collector(dry_run=True)
    saved, failed = collector.save_batch([{"isbn": "1"}, {"isbn": "2"}])
    assert saved == 2
    assert failed == 0


def test_save_batch_empty_returns_zero_zero():
    collector = _make_collector(dry_run=False)
    assert collector.save_batch([]) == (0, 0)


def test_save_batch_success_path():
    """upsert 정상 → (len, 0)."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    # save_with_size_fallback 의 saver 가 호출되면 그냥 통과
    with patch.object(smart_batch_collector, "with_retry", return_value=None) as mock_retry:
        saved, failed = collector.save_batch([{"isbn": "1"}, {"isbn": "2"}])
    assert saved == 2
    assert failed == 0
    assert mock_retry.called


def test_save_batch_permanent_error_counts_drop_not_silent():
    """영구 에러 시 silent 가 아니라 failed 반환."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    with patch.object(smart_batch_collector, "with_retry",
                      side_effect=FakeAPIError("23505")):
        saved, failed = collector.save_batch([{"isbn": "1"}, {"isbn": "2"}])
    assert saved == 0
    assert failed == 2


def test_save_batch_timeout_falls_back():
    """첫 시도 57014 → chunk 축소로 일부 성공 가능."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    call_count = {"n": 0}

    def fake_retry(fn, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise FakeAPIError("57014")
        return None  # 이후 chunk 는 모두 성공

    with patch.object(smart_batch_collector, "with_retry", side_effect=fake_retry):
        items = [{"isbn": str(i)} for i in range(50)]
        saved, failed = collector.save_batch(items)
    assert saved == 50
    assert failed == 0
    # 1 (50 실패) + 3 (20+20+10) = 4
    assert call_count["n"] == 4


def test_main_returns_one_on_drop_failed():
    """drop_failed > 0 이면 main() exit 1."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    collector.stats["drop_failed"] = 5
    with patch.object(smart_batch_collector, "SmartBatchCollector", return_value=collector):
        with patch("sys.argv", ["smart_batch_collector.py", "--status"]):
            rc = smart_batch_collector.main()
    assert rc == 0  # --status path는 0 (early return)


def test_main_returns_zero_on_clean_status():
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    with patch.object(smart_batch_collector, "SmartBatchCollector", return_value=collector):
        with patch("sys.argv", ["smart_batch_collector.py", "--status"]):
            rc = smart_batch_collector.main()
    assert rc == 0
