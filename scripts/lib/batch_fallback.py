"""배치 사이즈 fallback helper.

일시적 DB 부하로 큰 배치가 timeout 나면 자동으로 작은 단위로 쪼개서 재시도.
한 row 만 쪼개도 timeout 나면 포기하고 실패 카운트 증가.

이 helper 는 순수 python — DB 연결이나 pgcode 에 직접 의존하지 않는다.
호출자가 `saver(chunk)` 함수와 `is_timeout(exc)` 판별기를 주입한다.

표준 사용 패턴 (tier1_embedder, tier2_embedder, smart_batch_collector 공통):

    from scripts.lib.batch_fallback import save_with_size_fallback

    def saver(chunk):
        save_chunk(sb, chunk, dry_run=dry_run)

    saved, failed = save_with_size_fallback(
        items=batch,
        saver=saver,
        fallback_sizes=[20, 5],
        is_timeout=lambda e: str(getattr(e, "code", "") or "") == "57014",
    )
"""
from __future__ import annotations

from typing import Callable, List, Tuple, TypeVar

T = TypeVar("T")


def save_with_size_fallback(
    items: List[T],
    saver: Callable[[List[T]], None],
    fallback_sizes: List[int],
    is_timeout: Callable[[Exception], bool],
) -> Tuple[int, int]:
    """items 를 saver 로 저장. timeout 이면 fallback_sizes 순서로 쪼갠다.

    Args:
        items: 저장할 항목 리스트.
        saver: chunk 를 받아 저장하는 함수. 예외는 그대로 전파해야 한다.
        fallback_sizes: timeout 발생 시 시도할 chunk 크기들 (큰→작은 순서 권장,
            내부에서 _next_smaller 로 현재보다 작은 것을 골라 사용).
        is_timeout: 예외가 일시적 timeout 인지 판별. True 면 chunk 를 쪼개서
            재시도, False 면 영구 에러로 보고 그 chunk 를 실패 처리.

    Returns:
        (saved_count, failed_count). 합은 len(items).
    """
    total = len(items)
    if total == 0:
        return 0, 0

    # 첫 시도: 전체를 한 번에 (가장 빠른 경로)
    try:
        saver(items)
        return total, 0
    except Exception as e:
        if not is_timeout(e):
            return 0, total

    def _next_smaller(current: int):
        for fb in fallback_sizes:
            if fb < current:
                return fb
        return None

    saved = 0
    failed = 0
    initial = _next_smaller(total)
    queue: List[List[T]] = []

    if initial is None:
        # 이미 fallback 최소값 이하 — 1개씩 쪼개기
        if total > 1:
            queue = [items[j:j + 1] for j in range(total)]
        else:
            return 0, 1
    else:
        queue = [items[j:j + initial] for j in range(0, total, initial)]

    while queue:
        cur = queue.pop(0)
        size = len(cur)
        try:
            saver(cur)
            saved += size
            continue
        except Exception as e:
            if not is_timeout(e):
                failed += size
                continue
            nxt = _next_smaller(size)
            if nxt is None:
                if size > 1:
                    for j in range(size):
                        queue.append(cur[j:j + 1])
                    continue
                failed += 1
                continue
            for j in range(0, size, nxt):
                queue.append(cur[j:j + nxt])

    return saved, failed
