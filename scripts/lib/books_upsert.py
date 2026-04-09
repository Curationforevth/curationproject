"""B6: books 테이블 cross-source upsert — field-level richer merge.

smart_batch_collector (source='aladin') 와 data4library_discovery_collector
(source='data4library') 가 같은 ISBN 을 수집할 때, 순서에 따라 title/author/
cover_url 가 임의로 덮어쓰이는 문제를 방지한다.

정책 (Eden 결정: "내용을 보고 판단. 더 풍부한 쪽을 활용"):
  - 기존 row 가 없으면 → 그대로 insert
  - 기존 row 가 있으면 → 필드별 merge:
      * 문자열: 더 긴 쪽 (정보량 많음)
      * 숫자:   더 큰 쪽 (loan_count, sales_point 최신값 우선)
      * None/빈값: 반대쪽이 non-empty 면 반대쪽 채택
  - source 는 새 값이 우선 (최근 수집 소스 추적)
"""
from __future__ import annotations

from typing import Any

from .retry import with_retry

# 병합 대상 필드 — 나머지 (id, created_at, pk 등) 는 건드리지 않는다.
_STRING_FIELDS = ("title", "author", "publisher", "cover_url")
_NUMERIC_FIELDS = ("loan_count", "sales_point")


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def merge_richer(existing: dict, new: dict) -> dict:
    """existing + new → merged. existing 과 new 는 readonly 로 취급."""
    merged = dict(existing)
    for k, v in new.items():
        if k in _STRING_FIELDS:
            old = existing.get(k)
            if _is_empty(old):
                merged[k] = v
            elif _is_empty(v):
                pass  # 새 값이 비어있으면 기존 유지
            else:
                # 더 긴 문자열 채택
                if len(str(v)) > len(str(old)):
                    merged[k] = v
        elif k in _NUMERIC_FIELDS:
            old = existing.get(k) or 0
            new_v = v or 0
            if new_v > old:
                merged[k] = new_v
        elif k == "source":
            # 최근 수집 소스 기록
            if not _is_empty(v):
                merged[k] = v
        else:
            # 나머지 필드는 new 가 non-empty 일 때만 업데이트
            if not _is_empty(v):
                merged[k] = v
    return merged


def upsert_books_rich_merge(sb, rows: list[dict], chunk_size: int = 200) -> int:
    """rows 를 books 테이블에 upsert 하되, 기존 row 가 있으면 필드별 richer merge.

    1. chunk 의 ISBN 으로 기존 row 조회
    2. 각 new row 를 기존과 merge (없으면 그대로)
    3. upsert (on_conflict="isbn")

    Returns: 성공적으로 upsert 된 row 수.
    """
    if not rows:
        return 0

    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        isbns = [r["isbn"] for r in chunk if r.get("isbn")]
        if not isbns:
            continue

        # 기존 row 조회
        existing_rows = with_retry(lambda: (
            sb.table("books")
            .select("isbn, title, author, publisher, cover_url, loan_count, sales_point, source")
            .in_("isbn", isbns)
            .execute()
        ))
        existing_map = {r["isbn"]: r for r in (existing_rows.data or [])}

        merged_chunk = []
        for new_row in chunk:
            isbn = new_row.get("isbn")
            if not isbn:
                continue
            existing = existing_map.get(isbn)
            if existing:
                merged_chunk.append(merge_richer(existing, new_row))
            else:
                merged_chunk.append(new_row)

        with_retry(lambda c=merged_chunk: sb.table("books")
                   .upsert(c, on_conflict="isbn").execute())
        total += len(merged_chunk)

    return total
