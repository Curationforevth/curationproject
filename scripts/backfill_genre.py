#!/usr/bin/env python3
"""genre가 NULL인 책에 알라딘 API로 장르를 보강.

books 테이블에서 genre IS NULL인 책의 ISBN으로 알라딘 ItemLookUp을 호출,
categoryName을 가져와 genre 컬럼을 UPDATE.

사용법:
  python3 scripts/backfill_genre.py              # 기본 (100권)
  python3 scripts/backfill_genre.py --limit 500  # 500권
  python3 scripts/backfill_genre.py --dry-run    # DB 저장 없이 확인
  python3 scripts/backfill_genre.py --status     # 현황

의존성: supabase, python-dotenv
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ALADIN_TTB_KEY
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ALADIN_TTB_KEY = os.environ.get("ALADIN_TTB_KEY", "")

ITEM_LOOKUP_URL = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
REQUEST_DELAY = 0.3  # 알라딘 rate limit 대비


from typing import Optional

def aladin_lookup_isbn(isbn: str, max_retries: int = 3) -> Optional[dict]:
    """알라딘 ItemLookUp API로 ISBN 조회. 실패 시 None."""
    params = {
        "ttbkey": ALADIN_TTB_KEY,
        "itemIdType": "ISBN13" if len(isbn) == 13 else "ISBN",
        "ItemId": isbn,
        "output": "js",
        "Version": "20131101",
        "Cover": "None",
    }
    query = urllib.parse.urlencode(params)
    url = f"{ITEM_LOOKUP_URL}?{query}"

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("item", [])
            return items[0] if items else None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None


def main():
    parser = argparse.ArgumentParser(description="genre NULL 책에 알라딘으로 장르 보강")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    if args.status:
        total = sb.table("books").select("id", count="exact").execute()
        null_genre = sb.table("books").select("id", count="exact").is_("genre", "null").execute()
        has_isbn = sb.table("books").select("id", count="exact").is_("genre", "null").not_.is_("isbn", "null").execute()
        print(f"전체: {total.count}권")
        print(f"genre NULL: {null_genre.count}권 ({null_genre.count * 100 // total.count}%)")
        print(f"genre NULL + ISBN 있음 (보강 가능): {has_isbn.count}권")
        return

    if not ALADIN_TTB_KEY:
        print("ERROR: ALADIN_TTB_KEY 환경변수 필요")
        sys.exit(1)

    # genre NULL + ISBN 있는 책 조회
    books = sb.table("books").select("id,isbn,title,author").is_(
        "genre", "null"
    ).not_.is_("isbn", "null").order("sales_point", desc=True).limit(args.limit).execute()

    if not books.data:
        print("genre 보강할 책이 없습니다.")
        return

    print(f"대상: {len(books.data)}권")
    success = 0
    not_found = 0
    errors = 0

    for i, book in enumerate(books.data):
        isbn = book["isbn"]
        title = (book.get("title") or "?")[:30]

        item = aladin_lookup_isbn(isbn)
        if item is None:
            not_found += 1
            if not_found <= 5:
                print(f"  ✗ 알라딘 미발견: {title} (ISBN {isbn})")
            time.sleep(REQUEST_DELAY)
            continue

        category = item.get("categoryName", "")
        if not category:
            not_found += 1
            if not_found <= 5:
                print(f"  ✗ 카테고리 없음: {title}")
            time.sleep(REQUEST_DELAY)
            continue

        if args.dry_run:
            print(f"  [dry-run] {title} → {category[:50]}")
        else:
            try:
                sb.table("books").update({"genre": category}).eq("id", book["id"]).execute()
                success += 1
                if success <= 5 or success % 50 == 0:
                    print(f"  ✓ {title} → {category[:50]}")
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  ✗ DB 에러: {title} — {e}")

        time.sleep(REQUEST_DELAY)

    print(f"\n{'=' * 50}")
    print(f"{'(dry-run) ' if args.dry_run else ''}장르 보강 결과")
    print(f"{'=' * 50}")
    print(f"  대상: {len(books.data)}권")
    print(f"  성공: {success}권")
    print(f"  미발견: {not_found}권")
    print(f"  에러: {errors}건")


if __name__ == "__main__":
    sys.exit(main() or 0)
