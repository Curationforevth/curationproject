"""Fallback curation 시드.

books 테이블의 loan_count desc top 30 → fallback_curation 테이블.
Skip 유저 + 추천 서버 cold start fallback.

사용법:
  python3 scripts/seed_fallback_curation.py --dry-run
  python3 scripts/seed_fallback_curation.py
  python3 scripts/seed_fallback_curation.py --limit 50
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO, ".env"))


def rank_books_by_loan_count(books: list[dict]) -> list[dict]:
    """Sort by loan_count desc, drop rows with null loan_count, dedup by title."""
    valid = [b for b in books if b.get("loan_count") is not None]
    valid.sort(key=lambda b: b["loan_count"], reverse=True)
    seen_titles: set[str] = set()
    deduped = []
    for b in valid:
        title = (b.get("title") or "").strip()
        if title in seen_titles:
            continue
        seen_titles.add(title)
        deduped.append(b)
    return deduped


def build_fallback_rows(ranked: list[dict], limit: int = 30) -> list[dict]:
    """Build insert rows with sequential ranks starting at 1."""
    out = []
    for i, b in enumerate(ranked[:limit]):
        out.append({
            "rank": i + 1,
            "book_id": b["id"],
            "loan_count": b["loan_count"],
        })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args()

    from supabase import create_client
    sb = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )

    print(f"fetching books with loan_count (limit {args.limit * 3})...")
    res = (
        sb.table("books")
        .select("id, title, loan_count")
        .not_.is_("loan_count", "null")
        .order("loan_count", desc=True)
        .limit(args.limit * 3)
        .execute()
    )
    books = res.data or []
    print(f"  fetched {len(books)} candidate books")

    ranked = rank_books_by_loan_count(books)
    rows = build_fallback_rows(ranked, limit=args.limit)
    print(f"  ranked + truncated to {len(rows)} rows")

    if args.dry_run:
        print("(dry-run) sample rows:")
        for r in rows[:5]:
            print(f"  rank={r['rank']} book_id={r['book_id']} loan_count={r['loan_count']}")
        return

    sb.table("fallback_curation").delete().neq("rank", 0).execute()
    if rows:
        sb.table("fallback_curation").insert(rows).execute()
    print(f"✅ inserted {len(rows)} rows into fallback_curation")


if __name__ == "__main__":
    main()
