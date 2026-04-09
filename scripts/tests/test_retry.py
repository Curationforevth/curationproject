# scripts/tests/test_retry.py
"""retry wrapper 단위 테스트

두 축 검증:
  1. HTTP status (429, 5xx)
  2. Postgres SQLSTATE (57014 등)

Supabase-py 의 APIError.code 는 **문자열** Postgres SQLSTATE 인 점을
반드시 검증한다 (예전 버그 재발 방지).
"""
import pytest
from unittest.mock import MagicMock
from lib.retry import with_retry, _is_retryable


class FakeAPIError(Exception):
    """Supabase APIError 흉내 — code 는 기본 문자열."""
    def __init__(self, code, message="api error"):
        self.code = code
        self.message = message
        super().__init__(message)


class FakeHTTPError(Exception):
    """requests.HTTPError / httpx.HTTPStatusError 흉내."""
    def __init__(self, status_code):
        self.response = MagicMock(status_code=status_code)
        super().__init__(f"HTTP {status_code}")


# ----- _is_retryable: Postgres SQLSTATE -----

def test_is_retryable_pg_statement_timeout_string():
    """57014 는 문자열로 와도 retryable 이어야 한다 (실제 Supabase 동작)."""
    assert _is_retryable(FakeAPIError("57014")) is True


def test_is_retryable_pg_deadlock():
    assert _is_retryable(FakeAPIError("40P01")) is True


def test_is_retryable_pg_serialization_failure():
    assert _is_retryable(FakeAPIError("40001")) is True


def test_is_retryable_pg_too_many_connections():
    assert _is_retryable(FakeAPIError("53300")) is True


def test_is_retryable_pg_connection_failure():
    assert _is_retryable(FakeAPIError("08006")) is True


def test_not_retryable_pg_unique_violation():
    """23505 (unique_violation) 은 재시도 의미 없음."""
    assert _is_retryable(FakeAPIError("23505")) is False


def test_not_retryable_pg_invalid_input():
    """22P02 (invalid_text_representation) 은 영구 오류."""
    assert _is_retryable(FakeAPIError("22P02")) is False


# ----- _is_retryable: HTTP status -----

def test_is_retryable_http_504_via_response():
    assert _is_retryable(FakeHTTPError(504)) is True


def test_is_retryable_http_429_via_response():
    assert _is_retryable(FakeHTTPError(429)) is True


def test_is_retryable_http_502_via_response():
    assert _is_retryable(FakeHTTPError(502)) is True


def test_not_retryable_http_404():
    assert _is_retryable(FakeHTTPError(404)) is False


def test_not_retryable_http_400():
    assert _is_retryable(FakeHTTPError(400)) is False


def test_is_retryable_http_status_code_attribute_int():
    """APIError.status_code = 503 (int) 직접 부여 케이스."""
    e = Exception("boom")
    e.status_code = 503
    assert _is_retryable(e) is True


def test_is_retryable_http_status_code_attribute_string():
    """status_code 가 문자열로 온 경우도 처리."""
    e = Exception("boom")
    e.status_code = "503"
    assert _is_retryable(e) is True


# ----- _is_retryable: low-level -----

def test_is_retryable_connection_error():
    assert _is_retryable(ConnectionError("reset")) is True


def test_is_retryable_timeout_error():
    assert _is_retryable(TimeoutError("t/o")) is True


def test_is_retryable_oserror():
    assert _is_retryable(OSError("disk")) is True


def test_not_retryable_plain_value_error():
    assert _is_retryable(ValueError("bad")) is False


def test_not_retryable_bare_exception():
    assert _is_retryable(Exception("generic")) is False


# ----- with_retry: 실제 재시도 동작 -----

def test_success_on_first_try():
    fn = MagicMock(return_value="ok")
    result = with_retry(fn)
    assert result == "ok"
    assert fn.call_count == 1


def test_retry_on_pg_57014_then_success():
    """**핵심 회귀 테스트** — 57014 이후 재시도해서 성공해야 한다."""
    fn = MagicMock(side_effect=[FakeAPIError("57014"), "ok"])
    result = with_retry(fn, base_delay=0.001)
    assert result == "ok"
    assert fn.call_count == 2


def test_retry_on_http_502_then_success():
    fn = MagicMock(side_effect=[FakeHTTPError(502), "ok"])
    result = with_retry(fn, base_delay=0.001)
    assert result == "ok"
    assert fn.call_count == 2


def test_retry_on_connection_error_then_success():
    fn = MagicMock(side_effect=[ConnectionError("reset"), "ok"])
    result = with_retry(fn, base_delay=0.001)
    assert result == "ok"
    assert fn.call_count == 2


def test_no_retry_on_404():
    fn = MagicMock(side_effect=FakeHTTPError(404))
    with pytest.raises(FakeHTTPError):
        with_retry(fn, base_delay=0.001)
    assert fn.call_count == 1


def test_no_retry_on_unique_violation():
    fn = MagicMock(side_effect=FakeAPIError("23505"))
    with pytest.raises(FakeAPIError):
        with_retry(fn, base_delay=0.001)
    assert fn.call_count == 1


def test_exhausts_retries_on_57014():
    fn = MagicMock(side_effect=FakeAPIError("57014"))
    with pytest.raises(FakeAPIError):
        with_retry(fn, max_retries=3, base_delay=0.001)
    assert fn.call_count == 4  # 1 initial + 3 retries


def test_exhausts_retries_on_502():
    fn = MagicMock(side_effect=FakeHTTPError(502))
    with pytest.raises(FakeHTTPError):
        with_retry(fn, max_retries=3, base_delay=0.001)
    assert fn.call_count == 4
