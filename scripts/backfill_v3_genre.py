#!/usr/bin/env python3
"""book_v3_vectors에서 l1/l2가 NULL인 책의 장르 FK를 재매핑.

books 테이블에 genre가 채워졌지만, 이미 v3_vectors에 있어서
generate_book_v3_vectors.py가 건너뛴 책들의 l1_genre_id/l2_genre_id를 업데이트.

사용법:
  python3 scripts/backfill_v3_genre.py              # 실행
  python3 scripts/backfill_v3_genre.py --dry-run    # 확인만
  python3 scripts/backfill_v3_genre.py --status     # 현황
"""
import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# genre_parser는 scripts/lib에 있음
sys.path.insert(0, os.path.dirname(__file__))
from lib.genre_parser import parse_genre

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def main():
    parser = argparse.ArgumentParser(description="v3_vectors l1/l2 NULL 재매핑")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # NULL l1/l2 v3 rows
    null_v3 = sb.table("book_v3_vectors").select("book_id", count="exact").or_(
        "l1_genre_id.is.null,l2_genre_id.is.null"
    ).execute()

    if args.status:
        total_v3 = sb.table("book_v3_vectors").select("book_id", count="exact").execute()
        print(f"book_v3_vectors 전체: {total_v3.count}")
        print(f"l1/l2 NULL: {null_v3.count}")
        return

    if null_v3.count == 0:
        print("l1/l2 NULL인 v3 row 없음.")
        return

    # NULL인 book_ids 수집
    null_bids = []
    offset = 0
    while True:
        res = sb.table("book_v3_vectors").select("book_id").or_(
            "l1_genre_id.is.null,l2_genre_id.is.null"
        ).range(offset, offset + 999).execute()
        if not res.data:
            break
        null_bids.extend(r["book_id"] for r in res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    print(f"l1/l2 NULL인 v3 책: {len(null_bids)}권")

    # books 테이블에서 genre 가져오기
    books_genre = {}
    for i in range(0, len(null_bids), 50):
        chunk = null_bids[i:i + 50]
        res = sb.table("books").select("id,genre").in_("id", chunk).execute()
        for b in res.data:
            books_genre[b["id"]] = b.get("genre")

    # genre_embeddings lookup 테이블 구축
    # genre_embeddings는 paginate 필요 (825행)
    genre_lookup = {}
    offset = 0
    while True:
        genre_rows = sb.table("genre_embeddings").select("id,genre_text,level").range(offset, offset + 999).execute()
        if not genre_rows.data:
            break
        for g in genre_rows.data:
            genre_lookup[(g["genre_text"], g["level"])] = g["id"]
        if len(genre_rows.data) < 1000:
            break
        offset += 1000
    print(f"  genre_embeddings: {len(genre_lookup)}개")

    success = 0
    still_null = 0
    no_genre = 0

    for bid in null_bids:
        genre_str = books_genre.get(bid)
        if not genre_str:
            no_genre += 1
            continue

        l1, l2 = parse_genre(genre_str)
        l1_id = genre_lookup.get((l1, "l1")) if l1 else None
        l2_id = genre_lookup.get((l2, "l2")) if l2 else None

        if not l1_id and not l2_id:
            still_null += 1
            continue

        update = {}
        if l1_id:
            update["l1_genre_id"] = l1_id
        if l2_id:
            update["l2_genre_id"] = l2_id

        if args.dry_run:
            title_info = genre_str[:40] if genre_str else "?"
            print(f"  [dry-run] {bid[:8]}... l1={l1} l2={l2[:30] if l2 else None}")
        else:
            try:
                sb.table("book_v3_vectors").update(update).eq("book_id", bid).execute()
                success += 1
            except Exception as e:
                print(f"  ✗ {bid[:8]}... {e}")

    print(f"\n{'=' * 50}")
    print(f"{'(dry-run) ' if args.dry_run else ''}v3 l1/l2 재매핑 결과")
    print(f"{'=' * 50}")
    print(f"  대상: {len(null_bids)}권")
    print(f"  성공: {success}권")
    print(f"  genre 없음 (books): {no_genre}권")
    print(f"  genre_embeddings 매칭 실패: {still_null}권")


if __name__ == "__main__":
    main()
