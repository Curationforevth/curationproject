"""recommendation-server/engine/tier.py

User Tier 분기, Tier별 섹션 구성 규칙, CTA 문구 생성, 한국어 조사 처리.

Spec 참고:
- algorithm-design §4: Tier 임계 (3, 6)
- curation-system-design §6.2: Tier별 섹션 구성
- spec Phase 1B §6.2: 섹션 정의
"""
from __future__ import annotations

TIER1_THRESHOLD = 3
TIER2_THRESHOLD = 6


def user_tier_from_likes(total_likes: int) -> int:
    """좋아요 개수 → User Tier (0/1/2)."""
    if total_likes < TIER1_THRESHOLD:
        return 0
    if total_likes < TIER2_THRESHOLD:
        return 1
    return 2


def cta_for_tier(tier: int, total_likes: int) -> str | None:
    """Tier별 CTA 문구. Tier 2 는 None."""
    if tier == 0:
        remaining = TIER1_THRESHOLD - total_likes
        return f"좋아요 {remaining}권 더 누르면 비슷한 책 추천이 시작돼요"
    if tier == 1:
        remaining = TIER2_THRESHOLD - total_likes
        return f"좋아요 {remaining}권 더 평가하면 취향 추천이 시작돼요"
    return None


def korean_particle(word: str, with_batchim: str, without_batchim: str) -> str:
    """한국어 조사 처리. 단어 끝 받침 유무로 선택.
    비한글 끝 글자는 without_batchim 반환."""
    if not word:
        return without_batchim
    last = word[-1]
    if "가" <= last <= "힣":
        has_batchim = (ord(last) - 0xAC00) % 28 != 0
        return with_batchim if has_batchim else without_batchim
    return without_batchim


def sections_for_tier(tier: int) -> list[dict]:
    """Tier별 섹션 구성 (타입 + 큐레이션 개인화 힌트).

    Spec curation §6.2 준수. category_nav 는 Tier 0/1 만 (Tier 2 엔 없음).
    실제 books 리스트 채우기는 home.py 에서 수행.
    """
    if tier == 0:
        return [
            {"type": "trending"},
            {"type": "curation", "personalization": "general"},
            {"type": "curation", "personalization": "general"},
            {"type": "category_nav"},
        ]
    if tier == 1:
        return [
            {"type": "similar"},
            {"type": "curation", "personalization": "by_author"},
            {"type": "curation", "personalization": "by_l1"},
            {"type": "curation", "personalization": "general"},
            {"type": "category_nav"},
        ]
    # Tier 2
    return [
        {"type": "personal_recommend"},
        {"type": "curation", "personalization": "by_author"},
        {"type": "similar"},
        {"type": "curation", "personalization": "tier2+"},
        {"type": "trending"},
    ]


def similar_section_title(seed_title: str) -> str:
    """Tier 1/2 similar 섹션 제목 — '『X』과/와 비슷한 책'"""
    particle = korean_particle(seed_title, "과", "와")
    return f"『{seed_title}』{particle} 비슷한 책"
