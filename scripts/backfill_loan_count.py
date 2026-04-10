"""E3: books.loan_count 가 NULL 인 책을 정보나루 srchBooks 로 backfill.

aladin/kakao 경유로 들어온 책은 loan_count=NULL → fallback_curation 랭킹에서 빠짐.
정보나루 srchBooks 에 ISBN 을 query 로 넣어 loan_count 를 가져온다.

사용법:
  python3 scripts/backfill_loan_count.py --limit 100
  python3 scripts/backfill_loan_count.py --dry-run
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from lib.retry import with_retry
from lib.data4library_api import fetch_search, parse_book_docs

REQUEST_DELAY = 0.5


def fetch_loan_for_isbn(api_key: str, isbn: str) -> int | None:
    """정보나루 srchBooks 로 ISBN 검색 → loan_count 반환. 없으면 None."""
    data = fetch_search(api_key, keyword=isbn, page_size=1)
    rows = parse_book_docs(data)
    for r in rows:
        if r.get("isbn13") == isbn:
            return r.get("loan_count") or 0
    return None


def main():
    p = argparse.ArgumentParser(description="books.loan_count backfill (정보나루)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    sb = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )
    api_key = os.getenv("DATA4LIBRARY_API_KEY")
    if not api_key:
        print("❌ DATA4LIBRARY_API_KEY 누락")
        return 1

    # loan_count 가 NULL 인 책 조회
    books = with_retry(lambda: (
        sb.table("books")
        .select("id, isbn")
        .is_("loan_count", "null")
        .not_.is_("isbn", "null")
        .limit(args.limit)
        .execute()
    )).data or []
    print(f"대상: {len(books)}권")

    stats = {"updated": 0, "not_found": 0, "errors": 0}

    for i, book in enumerate(books):
        isbn = book.get("isbn")
        if not isbn:
            continue
        try:
            loan_count = fetch_loan_for_isbn(api_key, isbn)
            if loan_count is None:
                stats["not_found"] += 1
                continue
            if not args.dry_run:
                with_retry(lambda bid=book["id"], lc=loan_count: (
                    sb.table("books")
                    .update({"loan_count": lc})
                    .eq("id", bid)
                    .execute()
                ))
            stats["updated"] += 1
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(books)}] updated={stats['updated']}")
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"  ✗ {isbn}: {e}")
        time.sleep(REQUEST_DELAY)

    print(f"\n완료: {stats}")
    return 1 if stats["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
