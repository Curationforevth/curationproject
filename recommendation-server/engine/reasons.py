"""취향 발견 surfacing — 추천/유사 책의 대표 "좋아할 이유" 조회.

book_love_reasons.reason(제품 #1 차별점)을 응답 top-N 책에 붙여 유저가 "이 책이 왜
좋은지"를 보게 한다(PRODUCT_PLAN 핵심가치 #2 취향 발견). 요청경로에서 book_love_reasons
소량(top-N book_id) read 만 하며(벡터 아님, egress 미미), /recommend 응답은 캐시되므로
반복 read 는 없다.

주: 여기서 노출하는 건 **책 단위** 대표 이유다. "당신의 X 때문에" 식 유저별 매칭 이유는
인덱스에 reason 텍스트를 실어야(포맷 변경+재빌드) 하는 후속 확장(Phase 2).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pick_top_reasons(rows: list[dict]) -> dict[str, str]:
    """book_love_reasons 행들 → {book_id: 대표 reason}. 순수 함수(테스트 대상).

    책마다 user_mention_count 가 가장 높은(공유 축적이 많은) reason 을 대표로 고른다.
    동점/0이면 처음 만난 것을 유지(안정적).
    """
    best: dict[str, str] = {}
    best_mc: dict[str, int] = {}
    for r in rows:
        bid = r.get("book_id")
        reason = (r.get("reason") or "").strip()
        if not bid or not reason:
            continue
        mc = r.get("user_mention_count") or 0
        if bid not in best or mc > best_mc[bid]:
            best[bid] = reason
            best_mc[bid] = mc
    return best


def fetch_top_reasons(sb, book_ids: list[str]) -> dict[str, str]:
    """주어진 book_id 들의 대표 reason 을 Supabase 에서 조회. 실패해도 {} (비차단)."""
    ids = [b for b in dict.fromkeys(book_ids) if b]  # 중복 제거, 순서 유지
    if not ids:
        return {}
    try:
        res = (sb.table("book_love_reasons")
               .select("book_id,reason,user_mention_count")
               .in_("book_id", ids)
               .execute())
        return pick_top_reasons(res.data or [])
    except Exception as e:  # surfacing 실패가 추천 자체를 막지 않는다
        logger.warning("fetch_top_reasons 실패(추천은 계속): %s", e)
        return {}


def attach_reasons(sb, recs: list) -> list:
    """BookScore/SimilarBook 리스트에 reason 을 채운다(in-place). recs 를 그대로 반환."""
    if not recs:
        return recs
    reason_map = fetch_top_reasons(sb, [getattr(r, "book_id", None) for r in recs])
    for r in recs:
        if reason_map.get(r.book_id):
            r.reason = reason_map[r.book_id]
    return recs
