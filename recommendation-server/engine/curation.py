"""recommendation-server/engine/curation.py

큐레이션 풀에서 개인화 필터 + 가중 랜덤 sampling + 7일 디스카운트.

Spec curation-system §6.1-6.3 구현. sampling 은 priority + click_rate 가중치.
"""
from __future__ import annotations
import random
from typing import Optional

RECENT_CURATION_WINDOW_DAYS = 7


def filter_by_personalization(
    themes: list[dict],
    *,
    tier: int,
    top_authors: list[str],
    top_l1s: list[str],
) -> list[dict]:
    """Tier 와 유저 top preferences 기반으로 노출 가능한 theme 만 필터."""
    out: list[dict] = []
    for t in themes:
        p = t.get("personalization", "general")
        if p == "general":
            out.append(t)
        elif p == "tier1+" and tier >= 1:
            out.append(t)
        elif p == "tier2+" and tier >= 2:
            out.append(t)
        elif p == "by_author" and t.get("target_author") in top_authors:
            out.append(t)
        elif p == "by_l1" and t.get("target_l1") in top_l1s:
            out.append(t)
        elif p == "by_keyword" and t.get("target_keyword"):
            # Phase 1B 범위에서 by_keyword 는 구조만. 실제 매칭은 Phase 2.
            pass
    return out


def apply_recent_discount(themes: list[dict], recent_shown_ids: set[int]) -> list[dict]:
    """최근 7일 내 노출된 theme id 를 pool 에서 제외."""
    return [t for t in themes if t["id"] not in recent_shown_ids]


def weighted_sample_one(themes: list[dict]) -> Optional[dict]:
    """가중 랜덤 sampling 1개.

    weight = priority × (click_rate > 0.05 이면 ×1.5) × (by_* 개인화면 ×2.0)
    신간 가중치 0.05 (theme_type='genre_combo' + parameters 에 '신간')은 theme.priority 자체로 관리 가정.
    """
    if not themes:
        return None

    weights: list[float] = []
    for t in themes:
        w = t.get("priority", 1.0)
        if t.get("click_rate", 0.0) > 0.05 and t.get("shown_count", 0) >= 20:
            w *= 1.5
        if t.get("personalization") in ("by_l1", "by_author", "by_keyword"):
            w *= 2.0
        weights.append(max(w, 1e-6))

    return random.choices(themes, weights=weights, k=1)[0]
