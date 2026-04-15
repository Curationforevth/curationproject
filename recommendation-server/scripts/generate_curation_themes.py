"""recommendation-server/scripts/generate_curation_themes.py

Rule-based curation_themes 생성/갱신 (genre_combo, author, keyword).
weekly 실행. idempotent upsert via theme_key.

Cluster 타입은 별도 monthly script.

Eden feedback_batch_operations 준수:
- per-row try/except + 중간 commit
- 에러 시 continue (부분 실패 허용)
- 결과 count 로깅
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_supabase

MIN_GENRE_BOOKS = 10
MIN_AUTHOR_BOOKS = 3
MIN_KEYWORD_BOOKS = 5


def _upsert_theme(sb, *, theme_key, theme_type, title, description, parameters,
                  personalization, target_l1=None, target_author=None, target_keyword=None,
                  priority=1.0):
    row = {
        "theme_key": theme_key,
        "theme_type": theme_type,
        "title": title,
        "description": description,
        "selection_query": {"type": theme_type},
        "parameters": parameters,
        "personalization": personalization,
        "target_l1": target_l1,
        "target_author": target_author,
        "target_keyword": target_keyword,
        "priority": priority,
        "is_active": True,
    }
    sb.table("curation_themes").upsert(row, on_conflict="theme_key").execute()


def generate_genre_combo(sb) -> int:
    # books WHERE l1 IS NOT NULL AND l2 IS NOT NULL GROUP BY l1,l2 HAVING COUNT>=10
    # supabase-py 는 raw SQL 실행 불가 → RPC 또는 전체 fetch 후 in-memory aggregation
    # 여기선 전체 fetch (활성 ~9K rows).
    res = sb.table("books").select("l1,l2").not_.is_("l1", "null").not_.is_("l2", "null").execute()
    from collections import Counter
    counts = Counter((r["l1"], r["l2"]) for r in (res.data or []))
    created = 0
    for (l1, l2), cnt in counts.items():
        if cnt < MIN_GENRE_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"genre_combo|{l1}|{l2}",
                theme_type="genre_combo",
                title=f"{l1} · {l2}",
                description=f"{l1} 중 {l2} 분류의 책들",
                parameters={"l1": l1, "l2": l2},
                personalization="general",
                target_l1=l1,
            )
            created += 1
        except Exception as e:
            print(f"[skip] genre_combo {l1}|{l2}: {e}")
    return created


def generate_author(sb) -> int:
    res = sb.table("books").select("author").not_.is_("author", "null").execute()
    from collections import Counter
    counts = Counter(r["author"] for r in (res.data or []))
    created = 0
    for author, cnt in counts.items():
        if cnt < MIN_AUTHOR_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"author|{author}",
                theme_type="author",
                title=f"{author} 컬렉션",
                description=f"{author} 작가의 책들",
                parameters={"author": author},
                personalization="by_author",
                target_author=author,
            )
            created += 1
        except Exception as e:
            print(f"[skip] author {author}: {e}")
    return created


def generate_keyword(sb) -> int:
    # library_keywords TEXT[] → pg 배열 unnest 필요. in-memory.
    res = sb.table("books").select("library_keywords").not_.is_("library_keywords", "null").execute()
    from collections import Counter
    counts: Counter = Counter()
    for r in (res.data or []):
        for kw in (r.get("library_keywords") or []):
            counts[kw] += 1
    created = 0
    for kw, cnt in counts.items():
        if cnt < MIN_KEYWORD_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"keyword|{kw}",
                theme_type="keyword",
                title=kw,
                description=f"{kw} 관련 책들",
                parameters={"keyword": kw},
                personalization="by_keyword",
                target_keyword=kw,
            )
            created += 1
        except Exception as e:
            print(f"[skip] keyword {kw}: {e}")
    return created


def main(dry_run: bool = False):
    sb = get_supabase()
    print(f"[generate_themes] dry_run={dry_run}")
    if dry_run:
        print("dry-run: counting only")
    n_genre = generate_genre_combo(sb)
    print(f"  genre_combo: {n_genre}")
    n_author = generate_author(sb)
    print(f"  author:      {n_author}")
    n_keyword = generate_keyword(sb)
    print(f"  keyword:     {n_keyword}")
    total = n_genre + n_author + n_keyword
    print(f"  TOTAL upserts: {total}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
