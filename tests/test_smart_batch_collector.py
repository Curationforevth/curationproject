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


def test_main_status_returns_zero_regardless_of_drop_failed():
    """--status 는 collector.run 을 안 거치므로 항상 0."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    collector.stats["drop_failed"] = 5
    with patch.object(smart_batch_collector, "SmartBatchCollector", return_value=collector):
        with patch("sys.argv", ["smart_batch_collector.py", "--status"]):
            rc = smart_batch_collector.main()
    assert rc == 0


def test_main_returns_one_when_drop_failed_after_run():
    """run 끝나고 drop_failed > 0 이면 main() exit 1."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)

    def fake_run_phase():
        collector.stats["drop_failed"] = 3

    with patch.object(smart_batch_collector, "SmartBatchCollector", return_value=collector), \
         patch.object(collector, "load_known_isbns"), \
         patch.object(collector.state_mgr, "reset_expired_states"), \
         patch.object(collector, "run_item_list", side_effect=fake_run_phase), \
         patch.object(collector, "run_author_search"), \
         patch.object(collector, "run_keyword_search"), \
         patch.object(collector, "print_report"):
        with patch("sys.argv", ["smart_batch_collector.py", "--phase", "item_list"]):
            rc = smart_batch_collector.main()
    assert rc == 1


def test_main_returns_zero_when_clean_run():
    """drop_failed=0 이면 main() exit 0."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)
    with patch.object(smart_batch_collector, "SmartBatchCollector", return_value=collector), \
         patch.object(collector, "load_known_isbns"), \
         patch.object(collector.state_mgr, "reset_expired_states"), \
         patch.object(collector, "run_item_list"), \
         patch.object(collector, "run_author_search"), \
         patch.object(collector, "run_keyword_search"), \
         patch.object(collector, "print_report"):
        with patch("sys.argv", ["smart_batch_collector.py"]):
            rc = smart_batch_collector.main()
    assert rc == 0


# ============================================================
# KI-008: end-to-end (saved, failed) 튜플 흐름 검증
# ============================================================

def test_run_search_phase_propagates_save_batch_failure_end_to_end():
    """KI-008: _run_search_phase → save_batch → helper → drop_failed 가
    실제 메소드 호출 체인으로 흐르는지 검증.

    process_items 가 책 5권을 반환하고, with_retry 가 모두 23505 영구 에러를
    던지면 → save_batch 가 (0, 5) 반환 → run search phase 가
    self.stats['drop_failed'] += 5 처리해야 한다.
    """
    import smart_batch_collector
    collector = _make_collector(dry_run=False)

    # has_capacity 는 첫 호출만 True → 한 키워드만 처리하고 종료
    # has_capacity 는 키워드 진입 + 페이지 진입 양쪽에서 호출됨.
    # 첫 page 처리만 허용 (2번 True), 그 다음부터 False 로 종료.
    capacity_calls = {"n": 0}

    def fake_has_capacity():
        capacity_calls["n"] += 1
        return capacity_calls["n"] <= 2

    fake_books = [{"isbn": str(i), "title": "x", "author": "y"} for i in range(5)]
    collector.aladin.search_books = MagicMock(return_value=(["item"] * 5, 5))
    collector.state_mgr.get_state = MagicMock(return_value=None)
    collector.state_mgr.upsert_state = MagicMock()

    with patch.object(collector, "has_capacity", side_effect=fake_has_capacity), \
         patch.object(collector, "process_items", return_value=fake_books), \
         patch.object(smart_batch_collector, "with_retry",
                      side_effect=FakeAPIError("23505")), \
         patch("time.sleep"):
        collector._run_search_phase(["테스트키워드"], "keyword_search")

    # save_batch 가 모두 영구에러로 실패 → drop_failed=5, saved=0
    assert collector.stats["drop_failed"] == 5
    assert collector.stats["saved"] == 0


def test_run_search_phase_propagates_partial_save_success():
    """KI-008: timeout fallback 으로 일부 성공한 경우도 stats 가 정확히 분리."""
    import smart_batch_collector
    collector = _make_collector(dry_run=False)

    # has_capacity 는 키워드 진입 + 페이지 진입 양쪽에서 호출됨.
    # 첫 page 처리만 허용 (2번 True), 그 다음부터 False 로 종료.
    capacity_calls = {"n": 0}

    def fake_has_capacity():
        capacity_calls["n"] += 1
        return capacity_calls["n"] <= 2

    fake_books = [{"isbn": str(i), "title": "x", "author": "y"} for i in range(50)]
    collector.aladin.search_books = MagicMock(return_value=(["item"] * 50, 50))
    collector.state_mgr.get_state = MagicMock(return_value=None)
    collector.state_mgr.upsert_state = MagicMock()

    # 첫 시도 (50권) 57014 실패 → 20씩 쪼개서 성공
    retry_calls = {"n": 0}

    def fake_retry(fn, **kwargs):
        retry_calls["n"] += 1
        if retry_calls["n"] == 1:
            raise FakeAPIError("57014")
        return None

    with patch.object(collector, "has_capacity", side_effect=fake_has_capacity), \
         patch.object(collector, "process_items", return_value=fake_books), \
         patch.object(smart_batch_collector, "with_retry", side_effect=fake_retry), \
         patch("time.sleep"):
        collector._run_search_phase(["테스트키워드"], "keyword_search")

    assert collector.stats["saved"] == 50
    assert collector.stats["drop_failed"] == 0


def test_run_search_phase_api_failure_does_not_mark_completed():
    """A6/B3: transient API 실패 시 keyword 가 completed=True 로 저장되지 않음."""
    import smart_batch_collector
    # smart_batch_collector 가 실제 사용하는 예외 심볼을 그대로 재사용
    # (sys.path 차이로 scripts.lib.* vs lib.* 가 서로 다른 모듈이 될 수 있음)
    AladinAPIError = smart_batch_collector.AladinAPIError
    collector = _make_collector(dry_run=False)

    capacity_calls = {"n": 0}
    def fake_has_capacity():
        capacity_calls["n"] += 1
        return capacity_calls["n"] <= 2

    collector.aladin.search_books = MagicMock(
        side_effect=AladinAPIError("transient 500")
    )
    collector.state_mgr.get_state = MagicMock(return_value=None)
    collector.state_mgr.upsert_state = MagicMock()

    with patch.object(collector, "has_capacity", side_effect=fake_has_capacity), \
         patch("time.sleep"):
        collector._run_search_phase(["테스트키워드"], "keyword_search")

    assert collector.state_mgr.upsert_state.called
    last_call = collector.state_mgr.upsert_state.call_args
    assert last_call.kwargs["completed"] is False
    assert collector.stats["api_errors"] >= 1
