# scripts/lib/retry.py
"""Supabase 호출 retry wrapper — exponential backoff + jitter"""
import random
import time


def _is_retryable(exc):
    """재시도 대상 에러인지 판별

    Supabase Python 클라이언트는 내부적으로 requests/httpx를 사용하며,
    HTTP 에러는 다양한 형태로 올 수 있음:
      - requests.exceptions.HTTPError (response.status_code)
      - httpx.HTTPStatusError (response.status_code)
      - APIError 등 (status_code 또는 code 속성)
    """
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    # HTTP status code 추출 — 여러 라이브러리 대응
    status = None
    # requests.HTTPError / httpx.HTTPStatusError → exc.response.status_code
    resp = getattr(exc, 'response', None)
    if resp is not None:
        status = getattr(resp, 'status_code', None)
    # Supabase/PostgREST APIError → exc.code 또는 exc.status_code
    if status is None:
        status = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)

    if isinstance(status, int) and status in (429, 502, 503, 504):
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
