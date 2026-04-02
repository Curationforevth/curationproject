"""장르 파싱 및 HTML 클리닝 유틸리티.

v3 추천 엔진의 L1/L2 장르 분리 규칙:
- 접두어("국내도서", "외국도서", "eBook") 제거
- L1 = 첫 번째 레벨 (중분류)
- L2 = 나머지 전부 이어붙임 (소분류 이하)
"""
import re

KNOWN_PREFIXES = {"국내도서", "외국도서", "eBook"}


def parse_genre(genre_str):
    """장르 문자열을 L1, L2로 분리.

    Returns:
        (l1, l2) 튜플. 파싱 불가하면 (None, None).
    """
    if not genre_str or not genre_str.strip():
        return None, None

    parts = [p.strip() for p in genre_str.split(">")]

    if parts and parts[0] in KNOWN_PREFIXES:
        parts = parts[1:]

    if not parts:
        return None, None

    l1 = parts[0] if parts else None
    l2 = " ".join(parts[1:]) if len(parts) >= 2 else None

    return l1, l2


def clean_html(text):
    """HTML 태그 제거. None이면 빈 문자열 반환."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)
