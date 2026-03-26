"""
스마트 배치 수집기 — 알라딘 API → Supabase books 테이블

3단계로 인기 도서를 점진적으로 수집:
  Phase 1 (item_list): 카테고리 × QueryType 전수 스윕
  Phase 2 (author_search): DB 저자 + 큐레이션 저자 검색
  Phase 3 (keyword_search): 문학상/시리즈/장르/트렌드 키워드 검색

사용법:
  python3 scripts/smart_batch_collector.py                       # 전체 실행
  python3 scripts/smart_batch_collector.py --phase item_list     # Phase 1만
  python3 scripts/smart_batch_collector.py --phase author_search # Phase 2만
  python3 scripts/smart_batch_collector.py --phase keyword_search # Phase 3만
  python3 scripts/smart_batch_collector.py --status              # 진행 현황
  python3 scripts/smart_batch_collector.py --dry-run             # DB 저장 없이 테스트
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

# lib 모듈 import
sys.path.insert(0, os.path.dirname(__file__))
from lib.aladin_client import AladinClient
from lib.book_filter import is_non_book
from lib.title_cleaner import clean_title
from lib.dedup_checker import DeduplicateChecker
from lib.state_manager import StateManager
try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()

load_dotenv()

# ── 설정 ──────────────────────────────────────────────

CATEGORIES = {
    1: "소설/시/희곡",
    55889: "에세이",
    656: "인문학",
    336: "경제경영",
    987: "자기계발",
    798: "역사",
    1196: "종교/역학",
    517: "사회과학",
    2551: "예술/대중문화",
    170: "과학",
    2030: "IT모바일",
    1108: "가정/요리/뷰티",
    1230: "건강/취미/레저",
    # 2913: "만화",  # 제외
    13789: "유아",
    1137: "어린이",
    51377: "청소년",
}

QUERY_TYPES = ["Bestseller", "ItemNewAll", "ItemNewSpecial", "ItemEditorChoice", "BlogBest"]

MAX_PAGES = 4  # ItemList는 최대 4페이지 (200결과)
SEARCH_MAX_PAGES = 4  # ItemSearch도 4페이지
BATCH_SIZE = 50  # DB 배치 upsert 크기
API_CALL_DELAY = 0.15  # API 콜 사이 대기 (초)


class SmartBatchCollector:
    def __init__(self, dry_run=False, daily_target=0):
        self.dry_run = dry_run
        self.daily_target = daily_target

        # Supabase 클라이언트
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )

        # 알라딘 API 클라이언트
        self.aladin = AladinClient(os.getenv("ALADIN_TTB_KEY"))

        # 상태 관리
        self.state_mgr = StateManager(self.sb)

        # in-memory ISBN set (중복 방지)
        self.known_isbns = set()

        # 에디션 중복 체커 (ISBN이 다르지만 같은 작품인 경우 감지)
        self.dedup_checker = DeduplicateChecker(self.sb)

        # 통계
        self.stats = {
            "api_calls": 0,
            "raw_items": 0,
            "filtered_non_book": 0,
            "filtered_duplicate": 0,
            "filtered_edition_dup": 0,
            "filtered_no_isbn": 0,
            "saved": 0,
        }

    def has_capacity(self):
        """API 예산과 일일 목표 모두 체크"""
        if not self.aladin.has_budget():
            return False
        if self.daily_target > 0 and self.stats["saved"] >= self.daily_target:
            print(f"\n✅ 일일 목표 달성: {self.stats['saved']}/{self.daily_target}권")
            return False
        return True

    def load_known_isbns(self):
        """DB에서 기존 ISBN 전체 로드 → in-memory set"""
        print("📚 기존 ISBN 로드 중...")
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books").select("isbn").range(o, o + page_size - 1).execute())
            if not result.data:
                break
            for row in result.data:
                if row.get("isbn"):
                    self.known_isbns.add(row["isbn"])
            if len(result.data) < page_size:
                break
            offset += page_size

        print(f"   {len(self.known_isbns)}권 로드 완료")

        # 에디션 중복 인덱스 구축
        print("📖 에디션 중복 인덱스 구축 중...")
        title_count = self.dedup_checker.load_title_index()
        print(f"   {title_count}권 인덱스 완료\n")

    def process_items(self, items):
        """API 응답 아이템 → 필터 + 정제 → 저장 가능한 책 리스트"""
        books = []
        for item in items:
            self.stats["raw_items"] += 1

            # ISBN 확인
            isbn = item.get("isbn13") or item.get("isbn") or ""
            if not isbn:
                self.stats["filtered_no_isbn"] += 1
                continue

            # 중복 확인 (in-memory)
            if isbn in self.known_isbns:
                self.stats["filtered_duplicate"] += 1
                continue

            # 문제집/수험서 필터
            if is_non_book(item):
                self.stats["filtered_non_book"] += 1
                continue

            # 에디션 중복 체크 (ISBN은 다르지만 같은 작품)
            title = clean_title(item.get("title", ""))
            author = item.get("author", "")
            if self.dedup_checker.is_title_duplicate(title, author, isbn):
                self.stats["filtered_edition_dup"] += 1
                continue

            # 변환 + 제목 정제
            book = {
                "isbn": isbn,
                "title": clean_title(item.get("title", "")),
                "author": item.get("author", ""),
                "publisher": item.get("publisher", ""),
                "cover_url": item.get("cover", ""),
                "description": item.get("description", ""),
                "genre": item.get("categoryName", ""),
                "source": "aladin",
                "source_id": str(item.get("itemId", "")),
                "sales_point": item.get("salesPoint"),
            }

            books.append(book)
            self.known_isbns.add(isbn)
            # 에디션 중복 인덱스에도 등록 (세션 내 중복 방지)
            self.dedup_checker.register(title, author, isbn)

        return books

    def save_batch(self, books):
        """DB에 배치 upsert (카운트는 호출부에서 관리)"""
        if not books or self.dry_run:
            return

        try:
            with_retry(lambda: self.sb.table("books").upsert(
                books, on_conflict="isbn"
            ).execute())
        except Exception as e:
            print(f"    ✗ DB 저장 오류: {e}")
            # 개별 저장 fallback
            for book in books:
                try:
                    with_retry(lambda b=book: self.sb.table("books").upsert(
                        b, on_conflict="isbn"
                    ).execute())
                except Exception:
                    pass

    # ── Phase 1: ItemList 스윕 ──────────────────────────

    def run_item_list(self):
        """Phase 1: 라운드로빈 — 페이지별로 전 카테고리 순회"""
        print("=" * 60)
        print("Phase 1: ItemList 전카테고리 스윕 (라운드로빈)")
        print("=" * 60)

        for page in range(1, MAX_PAGES + 1):
            for cat_id, cat_name in CATEGORIES.items():
                for qt in QUERY_TYPES:
                    if not self.has_capacity():
                        print("\n⚠ 일일 API 한도 도달. 다음 실행에서 이어갑니다.")
                        return

                    items, total = self.aladin.fetch_item_list(qt, cat_id, page)

                    if not items:
                        continue

                    books = self.process_items(items)
                    self.stats["saved"] += len(books)

                    if books:
                        self.save_batch(books)
                        yield_rate = len(books) / len(items) if items else 0
                        print(f"  {cat_name} / {qt} p{page}: +{len(books)}권 (yield {yield_rate:.0%})")

                    time.sleep(API_CALL_DELAY)

    # ── Phase 2: 저자 기반 검색 ──────────────────────────

    def run_author_search(self):
        """Phase 2: DB 저자 + 큐레이션 저자 검색"""
        print("\n" + "=" * 60)
        print("Phase 2: 저자 기반 검색")
        print("=" * 60)

        # DB에서 저자 추출
        authors = set()
        offset = 0
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books").select("author").range(o, o + 999).execute())
            if not result.data:
                break
            for row in result.data:
                author = row.get("author", "")
                if author:
                    # "저자1, 저자2 (역할)" → 개별 저자 추출
                    for a in author.replace(" (지은이)", "").replace(" (옮긴이)", "").split(","):
                        a = a.strip()
                        if a and len(a) >= 2:
                            authors.add(a)
            if len(result.data) < 1000:
                break
            offset += 1000

        # 큐레이션 저자 추가
        keywords_path = os.path.join(os.path.dirname(__file__), "data", "search_keywords.json")
        with open(keywords_path, "r", encoding="utf-8") as f:
            keywords_data = json.load(f)

        for a in keywords_data.get("popular_korean_authors", []):
            authors.add(a)
        for a in keywords_data.get("popular_foreign_authors", []):
            authors.add(a)

        print(f"  검색 대상 저자: {len(authors)}명\n")

        self._run_search_phase(sorted(authors), "author_search")

    # ── Phase 3: 키워드 기반 검색 ──────────────────────────

    def run_keyword_search(self):
        """Phase 3: 문학상/시리즈/장르/트렌드 키워드 검색"""
        print("\n" + "=" * 60)
        print("Phase 3: 키워드 기반 검색")
        print("=" * 60)

        keywords_path = os.path.join(os.path.dirname(__file__), "data", "search_keywords.json")
        with open(keywords_path, "r", encoding="utf-8") as f:
            keywords_data = json.load(f)

        all_keywords = []
        for category in ["literary_prizes", "popular_series", "genre_keywords", "trending_themes"]:
            all_keywords.extend(keywords_data.get(category, []))

        print(f"  검색 키워드: {len(all_keywords)}개\n")

        self._run_search_phase(all_keywords, "keyword_search")

    def _run_search_phase(self, keywords, source_type):
        """ItemSearch 공통 로직"""
        for keyword in keywords:
            if not self.has_capacity():
                print("\n⚠ 일일 API 한도 도달. 다음 실행에서 이어갑니다.")
                return

            # 상태 확인
            state = self.state_mgr.get_state(
                source_type=source_type,
                search_keyword=keyword,
            )
            if state and state.get("completed"):
                continue

            last_page = state["last_page_fetched"] if state else 0
            total_found = state["total_items_found"] if state else 0
            unique_saved = state["unique_items_saved"] if state else 0

            page_new_count = 0  # 이 키워드에서 새로 발견한 수
            pages_fetched = 0   # 실제 페치한 페이지 수

            for page in range(last_page + 1, SEARCH_MAX_PAGES + 1):
                if not self.has_capacity():
                    break

                items, total = self.aladin.search_books(keyword, page)
                total_found += len(items)
                pages_fetched += 1

                if not items:
                    # 결과 없음 → 이 키워드 완료
                    self.state_mgr.upsert_state(
                        source_type=source_type,
                        search_keyword=keyword,
                        last_page_fetched=page,
                        total_items_found=total_found,
                        unique_items_saved=unique_saved,
                        completed=True,
                    )
                    break

                books = self.process_items(items)
                self.stats["saved"] += len(books)
                unique_saved += len(books)
                page_new_count += len(books)

                if books:
                    self.save_batch(books)
                    print(f"  '{keyword}' p{page}: +{len(books)}권")

                # 상태 저장
                self.state_mgr.upsert_state(
                    source_type=source_type,
                    search_keyword=keyword,
                    last_page_fetched=page,
                    total_items_found=total_found,
                    unique_items_saved=unique_saved,
                    completed=(page >= SEARCH_MAX_PAGES or len(items) < 50),
                )

                # 조기 종료: yield rate 10% 미만이면 소스 종료
                yield_rate = len(books) / len(items) if items else 0
                if yield_rate < 0.10:
                    self.state_mgr.upsert_state(
                        source_type=source_type,
                        search_keyword=keyword,
                        last_page_fetched=page,
                        total_items_found=total_found,
                        unique_items_saved=unique_saved,
                        completed=True,
                    )
                    break

                time.sleep(API_CALL_DELAY)

    # ── 리포트 ──────────────────────────────────────────

    def print_report(self):
        """실행 결과 리포트"""
        s = self.stats
        print(f"\n{'=' * 60}")
        print("수집 결과 리포트")
        print(f"{'=' * 60}")
        print(f"  API 호출: {self.aladin.api_calls}회 (잔여: {self.aladin.remaining_calls})")
        print(f"  원본 아이템: {s['raw_items']}건")
        print(f"  - ISBN 없음: {s['filtered_no_isbn']}건")
        print(f"  - 중복 (이미 DB): {s['filtered_duplicate']}건")
        print(f"  - 에디션 중복: {s['filtered_edition_dup']}건")
        print(f"  - 문제집/수험서: {s['filtered_non_book']}건")
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"  {prefix}새로 저장: {s['saved']}권")
        print(f"  DB 총 도서: {len(self.known_isbns)}권")
        print(f"{'=' * 60}")

    def show_status(self):
        """현재 수집 진행 상황 출력"""
        print("=" * 60)
        print("수집 진행 현황")
        print("=" * 60)

        # DB 총 도서 수
        result = with_retry(lambda: self.sb.table("books").select("id", count="exact").execute())
        print(f"\n  DB 총 도서: {result.count}권")

        # 소스별 요약
        summary = self.state_mgr.get_summary()
        if not summary:
            print("  수집 이력 없음\n")
            return

        print()
        for source_type, data in summary.items():
            phase_name = {
                "item_list": "Phase 1 (ItemList)",
                "author_search": "Phase 2 (저자 검색)",
                "keyword_search": "Phase 3 (키워드 검색)",
            }.get(source_type, source_type)

            print(f"  {phase_name}:")
            print(f"    소스: {data['completed']}/{data['total']}개 완료")
            print(f"    저장: {data['unique_saved']}권")
            print()


def main():
    parser = argparse.ArgumentParser(description="스마트 배치 수집기")
    parser.add_argument("--phase", choices=["item_list", "author_search", "keyword_search"])
    parser.add_argument("--status", action="store_true", help="진행 현황 조회")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--daily-target", type=int, default=0,
                        help="일일 신규 도서 목표 (0=무제한)")
    args = parser.parse_args()

    collector = SmartBatchCollector(dry_run=args.dry_run, daily_target=args.daily_target)

    if args.status:
        collector.show_status()
        return

    collector.load_known_isbns()
    collector.state_mgr.reset_expired_states(days=30)

    if args.dry_run:
        print("🧪 DRY-RUN 모드 — DB에 저장하지 않습니다\n")

    phase = args.phase or "all"

    if phase in ("all", "item_list"):
        collector.run_item_list()
    if phase in ("all", "author_search"):
        collector.run_author_search()
    if phase in ("all", "keyword_search"):
        collector.run_keyword_search()

    collector.print_report()


if __name__ == "__main__":
    main()
