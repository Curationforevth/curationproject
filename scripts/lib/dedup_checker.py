"""
수집 시점 중복 방지 모듈

같은 작품의 다른 에디션이 DB에 이미 있는지 확인.
ISBN 중복은 DB의 unique constraint로 처리되지만,
"1984" vs "1984 (특별판)"처럼 ISBN이 다른 동일 작품은 여기서 잡는다.

사용법 (legacy):
    checker = DeduplicateChecker(supabase_client)
    checker.load_title_index()
    if checker.is_title_duplicate(title, author, isbn):
        print("에디션 중복 → 스킵")

사용법 (Strategy C, 2026-04-16):
    action, existing_book_id = checker.check(title, author, isbn, loan_count)
    if action == DedupAction.NEW:
        # 신규 INSERT
    elif action == DedupAction.UPDATE_LOAN_COUNT:
        # 기존 existing_book_id 의 loan_count 만 업데이트
    else:  # SKIP
        # 동일 작품에 낮은 loan_count → 버림
"""

import re
from collections import defaultdict
from enum import Enum
from typing import Optional

from .title_cleaner import clean_title


class DedupAction(Enum):
    NEW = "new"                    # 신규 ISBN — INSERT
    SKIP = "skip"                  # 동일 작품 + 낮은 loan_count — 버림
    UPDATE_LOAN_COUNT = "update"   # 동일 작품 + 높은 loan_count — 기존 row loan_count UPDATE


def _normalize_for_dedup(title: str) -> str:
    """
    중복 비교용 제목 정규화 — dedup_books.py와 동일 로직

    괄호 안 에디션 정보 제거 + 부제 제거 → 핵심 제목만
    """
    normalized = title

    # 포맷 접두어 제거 (큰 글자책, eBook 등)
    normalized = re.sub(r"^(?:큰\s*글자(?:책)?|큰글자(?:책)?|eBook|ebook|E-?book)\s*", "", normalized, flags=re.IGNORECASE)

    # 괄호 안 에디션/판본/굿즈 정보 제거
    paren_patterns = [
        r"\s*\(.*?(?:판|에디션|리커버|양장|무선|문고|기념|보너스|수록|클래식|개정|완결|특전|리마스터|초판|복간|표지|디자인|일러스트|컬러|블랙|화이트|골드|실버|미니|빅|대형|포켓|뉴|신판|증보|축약|완역|전면|번역|역|합본|세트|큰글자|큰\s*글자|eBook|ebook).*?\)",
        r"\s*\(\d{4}\)",
        r"\s*\(\s*\)",
    ]
    for pattern in paren_patterns:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    # 콜론/대시 뒤 부제 제거 — 핵심 제목이 너무 짧아지면(3자 이하) 제거하지 않음
    # 예: "해리 포터: 마법사의 돌" vs "해리 포터: 비밀의 방" → 부제 제거 시 오매칭
    without_colon = re.sub(r"\s*[:：]\s*.+$", "", normalized)
    if len(without_colon.strip()) > 3:
        normalized = without_colon
    without_dash = re.sub(r"\s+[-—]\s+.+$", "", normalized)
    if len(without_dash.strip()) > 3:
        normalized = without_dash

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

    title_index 자료구조 (2026-04-16 업데이트):
      (정규화_제목, 정규화_저자) → [{'isbn', 'book_id', 'loan_count'}, ...]
    """

    def __init__(self, supabase_client):
        self.sb = supabase_client
        # (정규화 제목, 정규화 저자) → [{'isbn', 'book_id', 'loan_count'}, ...]
        self.title_index = defaultdict(list)

    def load_title_index(self):
        """DB에서 기존 제목+저자 인덱스 구축 (id, loan_count 도 함께 저장)."""
        offset = 0
        page_size = 1000
        count = 0

        while True:
            result = self.sb.table("books").select(
                "id, isbn, title, author, loan_count"
            ).range(offset, offset + page_size - 1).execute()

            if not result.data:
                break

            for row in result.data:
                # B3: 수집 시점에 clean_title 을 통과한 값과 동일한 key 로 매칭되도록
                # DB raw title 에도 clean_title 을 먼저 적용한다.
                title = clean_title(row.get("title", "") or "")
                author = row.get("author", "")
                isbn = row.get("isbn", "") or ""

                key = (_normalize_for_dedup(title), _normalize_author(author))
                self.title_index[key].append({
                    "isbn": isbn,
                    "book_id": row.get("id"),
                    "loan_count": row.get("loan_count") or 0,
                })
                count += 1

            if len(result.data) < page_size:
                break
            offset += page_size

        return count

    def is_title_duplicate(self, title: str, author: str, isbn: str = "") -> bool:
        """
        새 도서가 기존 도서의 에디션 중복인지 확인 (legacy API, 호환용 유지).

        Returns:
            True: 동일 작품이 이미 DB에 있음 → 스킵 권장
            False: 새 작품 또는 같은 ISBN → 수집 진행 (upsert 로 갱신)
        """
        key = (_normalize_for_dedup(clean_title(title or "")), _normalize_author(author))
        existing = self.title_index.get(key, [])

        if not existing:
            return False

        # 같은 ISBN이면 중복이 아니라 업데이트 (upsert 대상)
        if isbn:
            for e in existing:
                if e.get("isbn") == isbn:
                    return False

        # 다른 ISBN으로 이미 존재 → 에디션 중복
        return True

    def check(
        self, title: str, author: str, isbn: str, loan_count: int
    ) -> tuple[DedupAction, Optional[str]]:
        """동일 작품 판정 + loan_count 기반 action 결정 (Strategy C, 2026-04-16).

        Returns:
            (DedupAction.NEW, None)              — 신규 ISBN, INSERT 대상
            (DedupAction.SKIP, None)             — 동일 작품이 더 높은 loan_count 로 있음
            (DedupAction.UPDATE_LOAN_COUNT, book_id) — 동일 작품인데 새 loan_count 가 더 큼
        """
        key = (
            _normalize_for_dedup(clean_title(title or "")),
            _normalize_author(author or ""),
        )
        existing = self.title_index.get(key, [])

        if not existing:
            return (DedupAction.NEW, None)

        # 같은 ISBN 이면 upsert_books_rich_merge 가 자체적으로 loan_count merge 처리
        if isbn:
            for e in existing:
                if e.get("isbn") == isbn:
                    return (DedupAction.NEW, None)

        # 다른 ISBN — 최고 loan_count 에디션과 비교
        best = max(existing, key=lambda e: e.get("loan_count") or 0)
        best_lc = best.get("loan_count") or 0
        if loan_count > best_lc:
            return (DedupAction.UPDATE_LOAN_COUNT, best.get("book_id"))
        return (DedupAction.SKIP, None)

    def register(
        self, title: str, author: str, isbn: str,
        book_id: Optional[str] = None, loan_count: Optional[int] = None,
    ):
        """수집 완료 후 인덱스에 추가 (세션 내 중복 방지)."""
        key = (_normalize_for_dedup(clean_title(title or "")), _normalize_author(author))
        self.title_index[key].append({
            "isbn": isbn or "",
            "book_id": book_id,
            "loan_count": loan_count or 0,
        })

    def update_loan_count(self, book_id: str, loan_count: int):
        """세션 내 loan_count 갱신 (UPDATE_LOAN_COUNT 분기 후 호출)."""
        for entries in self.title_index.values():
            for e in entries:
                if e.get("book_id") == book_id:
                    e["loan_count"] = loan_count
                    return
