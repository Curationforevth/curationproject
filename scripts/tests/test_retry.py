# scripts/tests/test_retry.py
"""retry wrapper 단위 테스트"""
import pytest
from unittest.mock import MagicMock
from lib.retry import with_retry


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"API error {code}")


def test_success_on_first_try():
    fn = MagicMock(return_value="ok")
    result = with_retry(fn)
    assert result == "ok"
    assert fn.call_count == 1


def test_retry_on_502_then_success():
    fn = MagicMock(side_effect=[FakeAPIError(502), "ok"])
    result = with_retry(fn, base_delay=0.01)
    assert result == "ok"
    assert fn.call_count == 2


def test_retry_on_connection_error_then_success():
    fn = MagicMock(side_effect=[ConnectionError("reset"), "ok"])
    result = with_retry(fn, base_delay=0.01)
    assert result == "ok"
    assert fn.call_count == 2


def test_no_retry_on_4xx():
    fn = MagicMock(side_effect=FakeAPIError(404))
    with pytest.raises(FakeAPIError):
        with_retry(fn, base_delay=0.01)
    assert fn.call_count == 1


def test_exhausts_retries():
    fn = MagicMock(side_effect=FakeAPIError(502))
    with pytest.raises(FakeAPIError):
        with_retry(fn, max_retries=3, base_delay=0.01)
    assert fn.call_count == 4  # 1 initial + 3 retries
