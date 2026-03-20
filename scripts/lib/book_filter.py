"""
문제집/수험서/비도서 필터 모듈
취향 추천에 부적합한 아이템을 수집 단계에서 제거
"""

# 제목에서 감지할 키워드
SKIP_TITLE_KEYWORDS = [
    # 어학 시험
    "토익", "토플", "TOEIC", "TOEFL", "IELTS",
    # 시험/수험
    "기출문제", "모의고사", "기출 500",
    "한국사능력검정", "GSAT", "공무원",
    "수능", "EBS", "내신",
    # 추가 수험/학습
    "기출", "워크북", "실기", "필기",
    "교과서", "참고서", "학습지",
    "족보", "적중예상", "기본서",
    # 비도서
    "컬러링북", "다이어리", "플래너", "스케줄러",
    "악보", "기타 코드",
]

# 카테고리(장르)에서 감지할 키워드
SKIP_GENRE_KEYWORDS = [
    "수험", "문제집", "자격증", "검정시험",
    "대학입시", "공무원", "취업/수험서",
    "초등참고서", "중학참고서", "고등참고서",
    "유아학습", "어린이학습",
    "달력/기타", "잡지", "다이어리/팬시",
]


def is_non_book(item):
    """취향 추천에 부적합한 아이템이면 True 반환"""
    title = item.get("title", "") or ""
    genre = item.get("categoryName", "") or ""

    for kw in SKIP_TITLE_KEYWORDS:
        if kw in title:
            return True

    for kw in SKIP_GENRE_KEYWORDS:
        if kw in genre:
            return True

    return False
