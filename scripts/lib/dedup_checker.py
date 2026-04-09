"""
수집 시점 중복 방지 모듈

같은 작품의 다른 에디션이 DB에 이미 있는지 확인.
ISBN 중복은 DB의 unique constraint로 처리되지만,
"1984" vs "1984 (특별판)"처럼 ISBN이 다른 동일 작품은 여기서 잡는다.

사용법:
    checker = DeduplicateChecker(supabase_client)
    checker.load_title_index()  # 초기화 시 1회 호출

    # 수집 루프 내에서:
    if checker.is_title_duplicate(title, author):
        print("에디션 중복 → 스킵")
"""

import re
from collections import defaultdict

from .title_cleaner import clean_title


def _normalize_for_dedup(title: str) -> str:
    """
    중복 비교용 제목 정규화 — dedup_books.py와 동일 로직

    괄호 안 에디션 정보 제거 + 부제 제거 → 핵심 제목만
    """
    normalized = title

    # 괄호 안 에디션/판본/굿즈 정보 제거
    paren_patterns = [
        r"\s*\(.*?(?:판|에디션|리커버|양장|무선|문고|기념|보너스|수록|클래식|개정|완결|특전|리마스터|초판|복간|표지|디자인|일러스트|컬러|블랙|화이트|골드|실버|미니|빅|대형|포켓|뉴|신판|증보|축약|완역|전면|번역|역|합본|세트).*?\)",
        r"\s*\(\d{4}\)",
        r"\s*\(\s*\)",
    ]
    for pattern in paren_patterns:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    # 콜론/대시 뒤 부제 제거
    normalized = re.sub(r"\s*[:：]\s*.+$", "", normalized)
    normalized = re.sub(r"\s+[-—]\s+.+$", "", normalized)

    # 공백 정리
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def _normalize_author(author: str) -> str:
    """저자명 정규화 — 역할 제거, 첫 번째 저자만"""
    if not author:
        return ""
    author = re.sub(r"\s*\([^)]*\)", "", author)
    author = author.split(",")[0].strip()
    return author


class DeduplicateChecker:
    """
    수집 시점 에디션 중복 방지

    DB의 기존 제목을 정규화해서 in-memory 인덱스로 보유.
    새 도서 수집 시 정규화된 제목+저자로 기존 도서 존재 여부 확인.
    """

    def __init__(self, supabase_client):
        self.sb = supabase_client
        # (정규화 제목, 정규화 저자) → [isbn, ...]
        self.title_index = defaultdict(list)

    def load_title_index(self):
        """DB에서 기존 제목+저자 인덱스 구축"""
        offset = 0
        page_size = 1000
        count = 0

        while True:
            result = self.sb.table("books").select(
                "isbn, title, author"
            ).range(offset, offset + page_size - 1).execute()

            if not result.data:
                break

            for row in result.data:
                # B3: 수집 시점에 clean_title 을 통과한 값과 동일한 key 로 매칭되도록
                # DB raw title 에도 clean_title 을 먼저 적용한다.
                title = clean_title(row.get("title", "") or "")
                author = row.get("author", "")
                isbn = row.get("isbn", "")

                key = (_normalize_for_dedup(title), _normalize_author(author))
                self.title_index[key].append(isbn)
                count += 1

            if len(result.data) < page_size:
                break
            offset += page_size

        return count

    def is_title_duplicate(self, title: str, author: str, isbn: str = "") -> bool:
        """
        새 도서가 기존 도서의 에디션 중복인지 확인

        Returns:
            True: 동일 작품이 이미 DB에 있음 → 스킵 권장
            False: 새 작품 → 수집 진행
        """
        # B3: load_title_index 와 동일한 normalization 경로
        key = (_normalize_for_dedup(clean_title(title or "")), _normalize_author(author))
        existing = self.title_index.get(key, [])

        if not existing:
            return False

        # 같은 ISBN이면 중복이 아니라 업데이트 (upsert 대상)
        if isbn and isbn in existing:
            return False

        # 다른 ISBN으로 이미 존재 → 에디션 중복
        return True

    def register(self, title: str, author: str, isbn: str):
        """수집 완료 후 인덱스에 추가 (세션 내 중복 방지)"""
        key = (_normalize_for_dedup(clean_title(title or "")), _normalize_author(author))
        self.title_index[key].append(isbn)
