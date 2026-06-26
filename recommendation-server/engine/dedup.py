"""작품 단위 dedup — 같은 작품(다른 판본/ISBN → 다른 books.id UUID)이 추천/유사
목록에 중복 노출되는 것을 serving 레이어에서 제거한다.

배경: books 는 ISBN unique 로 upsert 되고 FK/인덱스는 UUID 로 키잉된다. 같은 작품의
다른 ISBN(판본)이 둘 다 NEW 로 수집되면 별개 UUID 로 인덱스에 들어간다(실측: 인덱스의
0.8%, 28그룹 — 동물농장 3판본 등). 수집/저장 식별자(UUID)는 그대로 두고, 응답을
조립할 때만 (정규화 title+author) 기준으로 중복 판본을 접는다. 스코어/캐시 의미 불변.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

_PAREN = re.compile(r"[\(\[].*?[\)\]]")  # (오리지널 초판본…), [세트] 등
_WS = re.compile(r"\s+")


def work_key(title: str, author: str) -> Optional[str]:
    """같은 작품의 다른 판본을 한 키로 묶는 정규화 키.

    - 괄호/대괄호 표기(판본·역할) 제거, 콜론 이후 부제 제거, 공백 제거, 소문자화.
    - 저자는 첫 저자만 사용(역자/편저 등 제외).
    - 제목이 비면 None 을 반환 → 호출측은 절대 묶지 않는다(빈 키로 뭉뚱그리기 방지).
    """
    t = (title or "")
    t = _PAREN.sub("", t)
    t = t.split(":")[0]
    t = _WS.sub("", t).lower()
    if not t:
        return None
    a = (author or "").split(",")[0]
    a = _PAREN.sub("", a)
    a = _WS.sub("", a).lower()
    return f"{t}|{a}"


def dedup_by_work(items: list, get_meta: Callable[[object], tuple]) -> list:
    """items 를 순서 유지하며 작품 단위로 dedup.

    get_meta(item) -> (title, author). 같은 work_key 가 이미 나왔으면 제거한다.
    목록이 점수 내림차순이라고 가정 → 먼저 온 것(상위 점수/판본)을 유지한다.
    work_key 가 None(제목 없음)이면 dedup 대상에서 제외(항상 통과).
    """
    seen: set = set()
    out: list = []
    for it in items:
        title, author = get_meta(it)
        k = work_key(title, author)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(it)
    return out


def dedup_similar(raw: list, books_meta: dict, seed_id: str, limit: int) -> list:
    """similar 결과 정제: (a) 시드 자신의 다른 판본 (b) 중복 판본 을 제거하고 limit 개로 자른다.

    raw: list[(book_id, score)] — limit 보다 넉넉히 over-fetch 한 것.
    "동물농장"을 보고 있는데 "동물농장(세트)"를 비슷한 책으로 띄우거나, 같은 작품의
    두 판본이 함께 뜨는 것을 막는다.
    """
    def _meta(bid):
        m = books_meta.get(bid, {})
        return (m.get("title", ""), m.get("author", ""))

    seed_k = work_key(*_meta(seed_id)) if seed_id in books_meta else None
    filtered = [
        (bid, score) for bid, score in raw
        if seed_k is None or work_key(*_meta(bid)) != seed_k
    ]
    deduped = dedup_by_work(filtered, lambda t: _meta(t[0]))
    return deduped[:limit]
