"""recommendation-server/scripts/generate_curation_themes.py

Rule-based curation_themes 생성 (genre_combo, author, keyword).
weekly 실행. **insert-only** — 기존 theme_key 행은 미접촉.

과거엔 theme_key upsert 로 전체 갱신했으나, LLM 품질 게이트
(curate_theme_quality.py)가 title/description 리라이트 + 저품질 is_active=false
처리를 하므로 upsert 는 매주 리라이트를 리셋하고 kill 된 테마를 부활시키는
함정이었다 → 신규 키만 insert 하고 기존 행 관리는 품질 게이트에 위임한다.

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


def _fetch_all(sb, table, select, filter_fn=None, batch_size=1000):
    """Paginated fetch (all rows) via range()."""
    rows = []
    start = 0
    while True:
        q = sb.table(table).select(select)
        if filter_fn:
            q = filter_fn(q)
        res = q.range(start, start + batch_size - 1).execute()
        data = res.data or []
        if not data:
            break
        rows.extend(data)
        if len(data) < batch_size:
            break
        start += batch_size
    return rows


def existing_theme_keys(sb) -> set:
    """이미 존재하는 theme_key 집합 — insert-only 판단용 (비활성 포함: kill 부활 금지)."""
    rows = _fetch_all(sb, "curation_themes", "theme_key")
    return {r["theme_key"] for r in rows}


def _insert_theme(sb, *, theme_key, theme_type, title, description, parameters,
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
    sb.table("curation_themes").insert(row).execute()


def generate_genre_combo(sb, existing: set, dry_run: bool = False) -> int:
    # books WHERE l1 IS NOT NULL AND l2 IS NOT NULL GROUP BY l1,l2 HAVING COUNT>=10
    # supabase-py 는 raw SQL 실행 불가 → RPC 또는 전체 fetch 후 in-memory aggregation
    # 여기선 전체 fetch (활성 ~9K rows).
    rows = _fetch_all(sb, "books", "l1,l2",
                      lambda q: q.not_.is_("l1", "null").not_.is_("l2", "null"))
    print(f"  fetched {len(rows)} books")
    from collections import Counter
    counts = Counter((r["l1"], r["l2"]) for r in rows)
    created = 0
    for (l1, l2), cnt in counts.items():
        if cnt < MIN_GENRE_BOOKS:
            continue
        key = f"genre_combo|{l1}|{l2}"
        if key in existing:
            continue  # 기존 행 미접촉(품질 게이트 관리 영역)
        if dry_run:
            created += 1
            continue
        try:
            _insert_theme(
                sb,
                theme_key=key,
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


def generate_author(sb, existing: set, dry_run: bool = False) -> int:
    rows = _fetch_all(sb, "books", "author",
                      lambda q: q.not_.is_("author", "null"))
    print(f"  fetched {len(rows)} books")
    from collections import Counter
    counts = Counter(r["author"] for r in rows)
    created = 0
    for author, cnt in counts.items():
        if cnt < MIN_AUTHOR_BOOKS:
            continue
        key = f"author|{author}"
        if key in existing:
            continue
        if dry_run:
            created += 1
            continue
        try:
            _insert_theme(
                sb,
                theme_key=key,
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


def generate_keyword(sb, existing: set, dry_run: bool = False) -> int:
    # library_keywords TEXT[] → pg 배열 unnest 필요. in-memory.
    rows = _fetch_all(sb, "books", "library_keywords",
                      lambda q: q.not_.is_("library_keywords", "null"))
    print(f"  fetched {len(rows)} books")
    from collections import Counter
    counts: Counter = Counter()
    for r in rows:
        for kw in (r.get("library_keywords") or []):
            counts[kw] += 1
    created = 0
    for kw, cnt in counts.items():
        if cnt < MIN_KEYWORD_BOOKS:
            continue
        key = f"keyword|{kw}"
        if key in existing:
            continue
        if dry_run:
            created += 1
            continue
        try:
            _insert_theme(
                sb,
                theme_key=key,
                theme_type="keyword",
                title=kw,
                description=f"{kw} 관련 책들",
                parameters={"keyword": kw},
                personalization="general",  # Phase 1B: by_keyword 매칭 미구현 → general 로 노출 (Phase 2 이월)
                target_keyword=kw,  # 유지 (Phase 2 대비)
            )
            created += 1
        except Exception as e:
            print(f"[skip] keyword {kw}: {e}")
    return created


def main(dry_run: bool = False):
    sb = get_supabase()
    print(f"[generate_themes] dry_run={dry_run} (insert-only — 기존 theme_key 미접촉)")
    existing = existing_theme_keys(sb)
    print(f"  existing themes: {len(existing)}")
    n_genre = generate_genre_combo(sb, existing, dry_run)
    print(f"  genre_combo 신규: {n_genre}")
    n_author = generate_author(sb, existing, dry_run)
    print(f"  author 신규:      {n_author}")
    n_keyword = generate_keyword(sb, existing, dry_run)
    print(f"  keyword 신규:     {n_keyword}")
    total = n_genre + n_author + n_keyword
    print(f"  TOTAL 신규 insert: {total}")
    if n_keyword:
        print("  → 신규 keyword 는 curate_theme_quality.py 가 심사/리라이트한다(워크플로 후속 스텝).")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
