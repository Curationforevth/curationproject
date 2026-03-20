"""
제목 정제 모듈
- 괄호 안 부가 정보 제거: (특별판), (양장), (개정판) 등
- 대시 뒤 굿즈/구성품 설명 제거
- 부제는 유지
"""

import re

# 제거할 괄호 안 패턴
REMOVE_PAREN_PATTERNS = [
    r"\(.*?(?:특별판|에디션|한정판|리커버|양장|무선|문고판|기념|보너스|수록|클래식|개정판|완결|특전).*?\)",
    r"\(리딩\)",
    r"\(리스닝\)",
    r"\(보카\)",
]

# 대시 뒤에서 제거할 패턴 (굿즈/구성품 설명)
REMOVE_DASH_PATTERNS = [
    r"\s*-\s*SL Comic.*$",
    r"\s*-\s*S코믹스.*$",
    r"\s*-\s*전\d+권.*$",
    r"\s*-\s*.*(?:카드|스탠드|소책자|북마크|띠지|포스터|포토|스티커|티켓|엽서|pp|수록|독점|강의|MP3|PDF|해설|기출|시험 대비).*$",
    r"\s*\+앱.*$",
    r",\s*완결\s*$",
]


def clean_title(title):
    """제목에서 부가 정보를 제거하고 핵심 제목만 반환"""
    cleaned = title

    for pattern in REMOVE_PAREN_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    for pattern in REMOVE_DASH_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
