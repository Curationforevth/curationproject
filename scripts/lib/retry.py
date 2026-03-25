# scripts/lib/retry.py
"""Supabase 호출 retry wrapper — exponential backoff + jitter"""
import random
import time


def _is_retryable(exc):
    """재시도 대상 에러인지 판별"""
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    code = getattr(exc, 'code', None)
    if code in (502, 503, 504):
        return True
    return False


def with_retry(fn, max_retries=3, base_delay=1.0):
    """fn()을 호출하고, 재시도 가능한 에러 시 exponential backoff로 재시도.

    재시도 대상: 502, 503, 504, ConnectionError, TimeoutError, OSError
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
