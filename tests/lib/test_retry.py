"""E1: retry whitelist 확장 + pg_code 로깅 테스트."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch
from scripts.lib.retry import with_retry, PG_RETRYABLE_SQLSTATES


class FakeAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"err {code}")


def test_55P03_lock_not_available_is_retryable():
    assert "55P03" in PG_RETRYABLE_SQLSTATES


def test_25P02_in_failed_sql_transaction_is_retryable():
    assert "25P02" in PG_RETRYABLE_SQLSTATES


def test_58030_io_error_is_retryable():
    assert "58030" in PG_RETRYABLE_SQLSTATES


def test_retry_55P03_succeeds_after_one_retry():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise FakeAPIError("55P03")
        return "ok"

    with patch("time.sleep"):
        result = with_retry(flaky)
    assert result == "ok"
    assert calls["n"] == 2


def test_non_whitelisted_code_raises_immediately():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise FakeAPIError("23505")  # unique violation — 영구

    with pytest.raises(FakeAPIError):
        with_retry(always_fail)
    assert calls["n"] == 1
