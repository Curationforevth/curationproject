"""save_with_size_fallback 단위 테스트."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from unittest.mock import MagicMock

from scripts.lib.batch_fallback import save_with_size_fallback


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"err {code}")


def _is_57014(e: Exception) -> bool:
    return str(getattr(e, "code", "") or "") == "57014"


def test_first_try_success():
    saver = MagicMock()
    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5], is_timeout=lambda e: False,
    )
    assert saved == 50
    assert failed == 0
    assert saver.call_count == 1


def test_empty_input():
    saver = MagicMock()
    saved, failed = save_with_size_fallback(
        [], saver, fallback_sizes=[20, 5], is_timeout=lambda e: False,
    )
    assert saved == 0
    assert failed == 0
    assert saver.call_count == 0


def test_timeout_falls_back_then_succeeds():
    """첫 50개 시도 → 57014 → 20씩 쪼개서 성공."""
    call_count = {"n": 0}

    def saver(chunk):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise FakeAPIError("57014")
        # 이후 모든 chunk 는 성공

    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5], is_timeout=_is_57014,
    )
    assert saved == 50
    assert failed == 0
    # 1 (50 실패) + 3 (20+20+10 성공) = 4
    assert call_count["n"] == 4


def test_permanent_error_drops_chunk():
    """57014 가 아닌 영구 에러 → fallback 안 하고 즉시 실패."""
    def saver(chunk):
        raise FakeAPIError("23505")  # unique_violation

    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5], is_timeout=_is_57014,
    )
    assert saved == 0
    assert failed == 50


def test_persistent_timeout_gives_up_at_singles():
    """모든 시도가 57014 → 1개씩 쪼개도 실패 → 전부 실패."""
    def saver(chunk):
        raise FakeAPIError("57014")

    items = list(range(5))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[20, 5], is_timeout=_is_57014,
    )
    assert saved == 0
    assert failed == 5


def test_partial_failure_mixed():
    """일부 row 만 영구 에러: 큰 batch timeout → 쪼갠 후 한 chunk 만 실패."""
    seen = {"calls": 0}

    def saver(chunk):
        seen["calls"] += 1
        if len(chunk) == 50:
            raise FakeAPIError("57014")
        # 5개 이하 chunk 중 첫번째만 영구 에러
        if 0 in chunk and len(chunk) <= 5:
            raise FakeAPIError("23505")
        # 나머지 OK

    items = list(range(50))
    saved, failed = save_with_size_fallback(
        items, saver, fallback_sizes=[5], is_timeout=_is_57014,
    )
    # 0~4 chunk 만 실패 (5권), 나머지 45권 성공
    assert saved == 45
    assert failed == 5


def test_single_item_timeout():
    """1개 item 이 처음부터 57014 → 즉시 1개 실패."""
    def saver(chunk):
        raise FakeAPIError("57014")

    saved, failed = save_with_size_fallback(
        [1], saver, fallback_sizes=[20, 5], is_timeout=_is_57014,
    )
    assert saved == 0
    assert failed == 1
