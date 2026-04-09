"""
정보나루 도서관 데이터 수집기

usageAnalysisList API 1콜로 키워드 + 함께 빌린 책을 동시 수집.
키워드는 Tier2 임베딩 보강용, 연관도서는 Phase 3 추천 엔진용.

사용법:
  python3 scripts/data4library_collector.py                  # 기본 (300권)
  python3 scripts/data4library_collector.py --limit 50       # 50권만
  python3 scripts/data4library_collector.py --limit 10000    # 백필
  python3 scripts/data4library_collector.py --status          # 진행 현황
  python3 scripts/data4library_collector.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install requests supabase python-dotenv
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    import requests
except ImportError:
    pass

# `lib.retry.with_retry` 는 hard dependency — silent no-op fallback 은 금지.
# (과거: 패스 문제로 retry 가 통째로 no-op 되어 수백 권 drop 하고도
#  exit 0 으로 끝나는 사고가 있었음. 반드시 실제 retry 가 돌아야 한다.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402


CO_LOAN_CAP = 50
REQUEST_DELAY = 0.5


# --- 순수 함수 (테스트 가능) ---

def parse_keywords(response):
    """API 응답에서 키워드 리스트 추출."""
    if not response:
        return []
    try:
        keywords = response.get("response", {}).get("keywords", [])
        return [
            kw["keyword"]["word"]
            for kw in keywords
            if kw.get("keyword", {}).get("word")
        ]
    except (KeyError, TypeError):
        return []


def parse_co_loan_books(response):
    """API 응답에서 함께 빌린 책 ISBN 리스트 추출. 최대 50개."""
    if not response:
        return []
    try:
        books = response.get("response", {}).get("coLoanBooks", [])
        isbns = [
            b["book"]["isbn13"]
            for b in books
            if b.get("book", {}).get("isbn13")
        ]
        return isbns[:CO_LOAN_CAP]
    except (KeyError, TypeError):
        return []


# --- 수집기 클래스 ---

class Data4LibraryCollector:
    DEFAULT_LIMIT = 300
    API_BASE = "http://data4library.kr/api"

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self._api_key = None  # lazy init — --status에서는 불필요
        self.stats = {
            "processed": 0, "keywords_found": 0,
            "co_loan_found": 0, "empty": 0, "errors": 0,
        }

    @property
    def api_key(self):
        if self._api_key is None:
            self._api_key = os.getenv("DATA4LIBRARY_API_KEY")
            if not self._api_key:
                print("❌ DATA4LIBRARY_API_KEY 환경변수가 설정되지 않았습니다.")
                sys.exit(1)
        return self._api_key

    def fetch_books_needing_collection(self, limit):
        """library_keywords가 NULL인 책 조회 (sales_point 높은 순)."""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("books")
                .select("id, isbn")
                .is_("library_keywords", "null")
                .not_.is_("isbn", "null")
                .order("sales_point", desc=True)
                .range(o, o + page_size - 1)
                .execute()
            ))
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size or len(all_books) >= limit:
                break
            offset += page_size
        return all_books[:limit]

    def fetch_usage(self, isbn):
        """usageAnalysisList API 호출 → (keywords, co_loan_isbns)."""
        url = f"{self.API_BASE}/usageAnalysisList"
        params = {
            "authKey": self.api_key,
            "isbn13": isbn,
            "format": "json",
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            if not r.text.strip():
                return [], []
            data = r.json()
            keywords = parse_keywords(data)
            co_loan = parse_co_loan_books(data)
            return keywords, co_loan
        except Exception as e:
            raise RuntimeError(f"API 호출 실패 ({isbn}): {e}")

    def _save(self, book_id, keywords, co_loan_isbns):
        """books 테이블에 키워드 + 연관도서 저장."""
        if self.dry_run:
            return
        update = {"library_keywords": keywords if keywords else []}
        if co_loan_isbns:
            update["related_isbns"] = {"co_loan": co_loan_isbns}
        with_retry(lambda: (
            self.sb.table("books")
            .update(update)
            .eq("id", book_id)
            .execute()
        ))

    def run(self, limit=None):
        """메인 실행."""
        limit = limit or self.DEFAULT_LIMIT
        print(f"🔍 정보나루 수집 대상 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_collection(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서의 정보나루 데이터가 수집 완료됨.")
            return 0

        for i, book in enumerate(books):
            isbn = book["isbn"]
            try:
                keywords, co_loan = self.fetch_usage(isbn)

                self._save(book["id"], keywords, co_loan)

                self.stats["processed"] += 1
                if keywords:
                    self.stats["keywords_found"] += 1
                if co_loan:
                    self.stats["co_loan_found"] += 1
                if not keywords and not co_loan:
                    self.stats["empty"] += 1

                if self.stats["processed"] % 50 == 0 or self.stats["processed"] <= 3:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{self.stats['processed']}/{len(books)}: "
                          f"kw={len(keywords)} co={len(co_loan)}")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({isbn}): {e}")

            time.sleep(REQUEST_DELAY)

        self._print_report(len(books))
        return 1 if self.stats["errors"] > 0 else 0

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}정보나루 수집 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}권")
        print(f"  처리 완료: {s['processed']}권")
        print(f"  키워드 있음: {s['keywords_found']}권")
        print(f"  연관도서 있음: {s['co_loan_found']}권")
        print(f"  데이터 없음: {s['empty']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = with_retry(lambda: self.sb.table("books")
                           .select("id", count="exact").execute())
        has_kw = with_retry(lambda: self.sb.table("books")
                            .select("id", count="exact")
                            .not_.is_("library_keywords", "null").execute())
        has_rel = with_retry(lambda: self.sb.table("books")
                             .select("id", count="exact")
                             .not_.is_("related_isbns", "null").execute())

        kw_pct = has_kw.count * 100 // total.count if total.count else 0
        rel_pct = has_rel.count * 100 // total.count if total.count else 0

        print(f"\n{'=' * 50}")
        print("정보나루 수집 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  키워드 수집 완료: {has_kw.count}권 ({kw_pct}%)")
        print(f"  키워드 미수집: {total.count - has_kw.count}권")
        print(f"  연관도서 있음: {has_rel.count}권 ({rel_pct}%)")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="정보나루 도서관 데이터 수집기")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수 (기본 300)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="진행 현황")
    args = parser.parse_args()

    collector = Data4LibraryCollector(dry_run=args.dry_run)

    if args.status:
        collector.show_status()
        return 0

    return collector.run(limit=args.limit) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
