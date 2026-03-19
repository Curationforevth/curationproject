"""
알라딘 베스트셀러 배치 수집 → Supabase DB 저장
사용법: python3 scripts/batch_collect_aladin.py
"""

import os
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv
from supabase import create_client

# .env 로드
load_dotenv()

ALADIN_TTB_KEY = os.getenv("ALADIN_TTB_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def fetch_aladin_bestsellers(max_results=50, start=1):
    """알라딘 베스트셀러 리스트 가져오기"""
    params = urllib.parse.urlencode({
        "ttbkey": ALADIN_TTB_KEY,
        "QueryType": "Bestseller",
        "MaxResults": max_results,
        "start": start,
        "SearchTarget": "Book",
        "output": "js",
        "Version": "20131101",
    })
    url = f"http://www.aladin.co.kr/ttb/api/ItemList.aspx?{params}"

    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode("utf-8"))

    return data.get("item", [])


def transform_to_book(item):
    """알라딘 API 응답 → books 테이블 형식으로 변환"""
    isbn = item.get("isbn13") or item.get("isbn") or ""
    if not isbn:
        return None

    return {
        "isbn": isbn,
        "title": item.get("title", ""),
        "author": item.get("author", ""),
        "publisher": item.get("publisher", ""),
        "cover_url": item.get("cover", ""),
        "description": item.get("description", ""),
        "genre": item.get("categoryName", ""),
        "source": "aladin",
        "source_id": str(item.get("itemId", "")),
    }


def save_to_db(books):
    """books 테이블에 upsert (ISBN 기준 중복 방지)"""
    saved = 0
    skipped = 0

    for book in books:
        if not book:
            skipped += 1
            continue

        try:
            supabase.table("books").upsert(
                book,
                on_conflict="isbn"
            ).execute()
            saved += 1
            print(f"  ✓ {book['title'][:30]}")
        except Exception as e:
            print(f"  ✗ {book.get('title', '?')[:30]} — {e}")
            skipped += 1

    return saved, skipped


def main():
    print("=" * 50)
    print("알라딘 베스트셀러 배치 수집")
    print("=" * 50)

    total_saved = 0
    total_skipped = 0

    # 50개씩 2페이지 = 100권
    for page in range(1, 3):
        print(f"\n📖 페이지 {page} 수집 중...")
        items = fetch_aladin_bestsellers(max_results=50, start=page)
        print(f"   {len(items)}권 가져옴")

        books = [transform_to_book(item) for item in items]
        saved, skipped = save_to_db(books)

        total_saved += saved
        total_skipped += skipped

    print(f"\n{'=' * 50}")
    print(f"완료! 저장: {total_saved}권, 스킵: {total_skipped}권")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
