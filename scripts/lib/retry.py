# scripts/lib/retry.py
"""Supabase 호출 retry wrapper — exponential backoff + jitter

재시도 판단은 두 축으로 이뤄진다:
  1) HTTP status (requests.HTTPError / httpx.HTTPStatusError / APIError.status_code)
     → 429, 502, 503, 504 는 retryable
  2) Postgres SQLSTATE (PostgREST APIError.code, 문자열)
     → statement_timeout, deadlock, too_many_connections 등 일시적 상황은 retryable

**왜 두 축인가:** supabase-py 의 `APIError.code` 는 PostgREST 가 돌려준 **Postgres SQLSTATE
문자열** 이지 HTTP status 가 아니다 (예: '57014'). 예전 구현은 `isinstance(code, int)` 로
걸러서 모든 PG 에러를 non-retryable 로 처리했고, 그 결과 DB 부하로 인한 statement_timeout
이 한 번만 나면 배치가 통째로 드롭됐다.
"""
import random
import time


# Postgres SQLSTATE 코드 중 일시적/재시도 가치 있는 것들
# https://www.postgresql.org/docs/current/errcodes-appendix.html
PG_RETRYABLE_SQLSTATES = frozenset({
    "57014",  # query_canceled (statement_timeout 포함)
    "57P01",  # admin_shutdown
    "53300",  # too_many_connections
    "53400",  # configuration_limit_exceeded
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "08000",  # connection_exception
    "08003",  # connection_does_not_exist
    "08006",  # connection_failure
})

# 재시도 가치 있는 HTTP status
HTTP_RETRYABLE = frozenset({429, 502, 503, 504})


def _is_retryable(exc):
    """재시도 대상 에러인지 판별.

    판단 순서:
      1. low-level network/OS 예외 → True
      2. HTTP status (int 또는 숫자 문자열) in HTTP_RETRYABLE → True
      3. Postgres SQLSTATE (문자열) in PG_RETRYABLE_SQLSTATES → True
      4. 그 외 → False
    """
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    # --- HTTP status 축 ---
    http_status = None
    resp = getattr(exc, "response", None)
    if resp is not None:
        http_status = getattr(resp, "status_code", None)
    if http_status is None:
        # APIError 같은 경우 status_code 속성이 따로 있을 수 있음
        http_status = getattr(exc, "status_code", None)

    if http_status is not None:
        try:
            http_status_int = int(http_status)
            if http_status_int in HTTP_RETRYABLE:
                return True
        except (TypeError, ValueError):
            pass

    # --- Postgres SQLSTATE 축 ---
    # PostgREST APIError.code 는 문자열 SQLSTATE
    pg_code = getattr(exc, "code", None)
    if pg_code is not None:
        if str(pg_code) in PG_RETRYABLE_SQLSTATES:
            return True

    return False


def with_retry(fn, max_retries=3, base_delay=1.0):
    """fn()을 호출하고, 재시도 가능한 에러 시 exponential backoff로 재시도.

    재시도 대상: 429, 502, 503, 504, ConnectionError, TimeoutError, OSError
    즉시 실패: 그 외 모든 에러 (4xx 등)

    Args:
        fn: 인자 없는 callable (lambda로 감싸서 전달)
        max_retries: 최대 재시도 횟수 (기본 3)
        base_delay: 첫 재시도 대기 시간 (초, 기본 1.0)
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt) * (1 + random.uniform(-0.3, 0.3))
            print(f"  ⚠ Supabase 재시도 {attempt + 1}/{max_retries} ({type(e).__name__}), {delay:.1f}초 대기...")
            time.sleep(delay)
    raise last_exc
