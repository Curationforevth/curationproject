"""
문제집/수험서 등 취향 추천에 부적합한 도서를 DB에서 제거
사용법:
  python3 scripts/remove_non_books.py           # 미리보기
  python3 scripts/remove_non_books.py --apply   # 실제 삭제
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(__file__))
from lib.book_filter import SKIP_TITLE_KEYWORDS, SKIP_GENRE_KEYWORDS

load_dotenv()
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)


def find_non_books(books):
    """문제집/수험서 필터링 — lib/book_filter.py와 동일 키워드 사용"""
    results = []
    for book in books:
        title = book.get("title", "") or ""
        genre = book.get("genre", "") or ""

        matched_by = None
        for kw in SKIP_TITLE_KEYWORDS:
            if kw in title:
                matched_by = f"제목: '{kw}'"
                break

        if not matched_by:
            for kw in SKIP_GENRE_KEYWORDS:
                if kw in genre:
                    matched_by = f"카테고리: '{kw}'"
                    break

        if matched_by:
            results.append({
                "id": book["id"],
                "title": title,
                "genre": genre,
                "matched_by": matched_by,
            })

    return results


def main():
    apply = "--apply" in sys.argv
    mode = "적용 모드" if apply else "미리보기 모드 (--apply로 실제 삭제)"

    print(f"🗑️  문제집/수험서 제거 — {mode}")
    print("=" * 60)

    res = supabase.table("books").select("id, title, genre").execute()
    non_books = find_non_books(res.data)

    if not non_books:
        print("제거할 도서가 없어요!")
        return

    print(f"\n제거 대상: {len(non_books)}권\n")
    for i, b in enumerate(non_books, 1):
        print(f"  {i}. {b['title'][:60]}")
        print(f"     매칭: {b['matched_by']}")
        print()

    if apply:
        print("DB에서 삭제 중...")
        for b in non_books:
            supabase.table("books").delete().eq("id", b["id"]).execute()
            print(f"  ✓ 삭제: {b['title'][:40]}")

        print(f"\n완료! {len(non_books)}권 삭제됨")
    else:
        print(f"위 {len(non_books)}건을 삭제하려면: python3 scripts/remove_non_books.py --apply")


if __name__ == "__main__":
    main()
