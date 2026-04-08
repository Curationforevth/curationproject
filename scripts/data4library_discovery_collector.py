"""정보나루 신규 ISBN 발견 수집기 (discovery).

기존 `data4library_collector.py` (책별 키워드 enrich) 와 별개. 이 스크립트는
새 책을 발견해서 books 테이블에 채우는 게 목적.

Tier 1: loanItemSrch — KDC × 기간 인기 대출 (주 발견 소스)
Tier 2: recommandList — Tier 1 결과의 백카탈로그/연관작 (Task 4)
Tier 3: monthlyKeywords + srchBooks — 트렌드 키워드 확장 (Task 5)

성인 단행본만 수집 (addition_symbol[0] == '0').
에디션 중복은 dedup_checker로 제거.

사용법:
  python3 scripts/data4library_discovery_collector.py --tier 1 --dry-run
  python3 scripts/data4library_discovery_collector.py --tier 1
  python3 scripts/data4library_discovery_collector.py --tier 1 --period-days 180 --pages 2
  python3 scripts/data4library_discovery_collector.py --status
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.lib.data4library_api import (
    fetch_loan_item_page,
    parse_book_docs,
    is_adult_general,
)
from scripts.lib.dedup_checker import DeduplicateChecker

load_dotenv(os.path.join(REPO, ".env"))


PAGE_SIZE = 50
REQUEST_DELAY = 0.5


KDC_BUCKETS = [
    {"kdc": "0", "label": "총류"},
    {"kdc": "1", "label": "철학"},
    {"kdc": "2", "label": "종교"},
    {"kdc": "3", "label": "사회과학"},
    {"kdc": "4", "label": "자연과학"},
    {"kdc": "5", "label": "기술과학"},
    {"kdc": "6", "label": "예술"},
    {"kdc": "7", "label": "언어"},
    {"kdc": "8", "label": "문학"},
    {"kdc": "9", "label": "역사"},
]


def dedup_in_batch_by_isbn(rows: list[dict]) -> list[dict]:
    """In-batch ISBN dedup. Keeps the row with the highest loan_count."""
    by_isbn: dict[str, dict] = {}
    for r in rows:
        isbn = (r.get("isbn13") or "").strip()
        if not isbn:
            continue
        existing = by_isbn.get(isbn)
        if existing is None or (r.get("loan_count") or 0) > (existing.get("loan_count") or 0):
            by_isbn[isbn] = r
    return list(by_isbn.values())


def extract_first_author(author_raw: Optional[str]) -> str:
    """'지은이: 유발 하라리 ;옮긴이: 조현욱' -> '유발 하라리'."""
    if not author_raw:
        return ""
    s = re.sub(r"^(지은이|저자|글|원작)\s*[:：]\s*", "", author_raw)
    s = re.split(r"[;,]", s)[0]
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_for_upsert(parsed: dict) -> dict:
    """Convert a parsed loanItem dict to a books-table row."""
    # sales_point bootstraps from loan_count; later Aladin enrichment
    # may overwrite with retailer sales rank.
    return {
        "isbn": parsed["isbn13"],
        "title": parsed.get("title") or "",
        "author": extract_first_author(parsed.get("author_raw")),
        "publisher": parsed.get("publisher"),
        "cover_url": parsed.get("cover_url"),
        "loan_count": parsed.get("loan_count") or 0,
        "sales_point": parsed.get("loan_count") or 0,
    }


class DiscoveryCollector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key: Optional[str] = None
        self._sb = None
        self._dedup: Optional[DeduplicateChecker] = None
        self.stats = {
            "fetched_raw": 0,
            "filtered_children": 0,
            "filtered_isbn_dup": 0,
            "filtered_edition_dup": 0,
            "upserted": 0,
            "errors": 0,
        }

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = os.getenv("DATA4LIBRARY_API_KEY")
            if not self._api_key:
                print("ERROR: DATA4LIBRARY_API_KEY not set", file=sys.stderr)
                sys.exit(1)
        return self._api_key

    @property
    def sb(self):
        if self._sb is None:
            from supabase import create_client
            self._sb = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            )
        return self._sb

    @property
    def dedup(self) -> DeduplicateChecker:
        if self._dedup is None:
            print("📚 dedup index 로딩 중...")
            self._dedup = DeduplicateChecker(self.sb)
            count = self._dedup.load_title_index()
            print(f"   {count}권 인덱스 완료")
        return self._dedup

    def fetch_tier1(self, period_days: int = 30, pages: int = 1) -> list[dict]:
        """Tier 1: loanItemSrch × KDC 0~9 × pages."""
        end = datetime.now().date()
        start = end - timedelta(days=period_days)
        start_dt = start.strftime("%Y-%m-%d")
        end_dt = end.strftime("%Y-%m-%d")

        all_rows: list[dict] = []
        for bucket in KDC_BUCKETS:
            for page in range(1, pages + 1):
                try:
                    raw = fetch_loan_item_page(
                        api_key=self.api_key,
                        page_no=page, page_size=PAGE_SIZE,
                        start_dt=start_dt, end_dt=end_dt,
                        kdc=bucket["kdc"],
                    )
                    parsed = parse_book_docs(raw)
                    print(f"  [KDC {bucket['kdc']} {bucket['label']}] page {page}: {len(parsed)} books")
                    all_rows.extend(parsed)
                    self.stats["fetched_raw"] += len(parsed)
                except Exception as e:
                    print(f"  ✗ [KDC {bucket['kdc']}] page {page}: {e}")
                    self.stats["errors"] += 1
                time.sleep(REQUEST_DELAY)
        return all_rows

    def filter_and_upsert(self, parsed_rows: list[dict]) -> int:
        """Apply children filter, batch ISBN dedup, edition dedup, then upsert."""
        adult_rows = [r for r in parsed_rows if is_adult_general(r)]
        self.stats["filtered_children"] += len(parsed_rows) - len(adult_rows)
        print(f"  성인 단행본 필터: {len(adult_rows)}/{len(parsed_rows)}")

        by_isbn = dedup_in_batch_by_isbn(adult_rows)
        self.stats["filtered_isbn_dup"] += len(adult_rows) - len(by_isbn)
        print(f"  배치 ISBN dedup: {len(by_isbn)}/{len(adult_rows)}")

        if not by_isbn:
            return 0

        unique_rows: list[dict] = []
        for r in by_isbn:
            title = r.get("title") or ""
            author = extract_first_author(r.get("author_raw"))
            isbn = r["isbn13"]
            if self.dedup.is_title_duplicate(title, author, isbn):
                self.stats["filtered_edition_dup"] += 1
                continue
            unique_rows.append(r)
            self.dedup.register(title, author, isbn)
        print(f"  에디션 dedup: {len(unique_rows)}/{len(by_isbn)}")

        if not unique_rows:
            return 0

        rows = [sanitize_for_upsert(r) for r in unique_rows]
        if self.dry_run:
            print(f"  (dry-run) would upsert {len(rows)} rows")
            print(f"  sample: {rows[0]}")
            return len(rows)

        upserted = 0
        for i in range(0, len(rows), 200):
            chunk = rows[i:i + 200]
            self.sb.table("books").upsert(chunk, on_conflict="isbn").execute()
            upserted += len(chunk)
        self.stats["upserted"] += upserted
        return upserted

    def show_status(self):
        total = self.sb.table("books").select("id", count="exact").execute()
        with_loan = (
            self.sb.table("books")
            .select("id", count="exact")
            .not_.is_("loan_count", "null")
            .execute()
        )
        print(f"books: {total.count}, with loan_count: {with_loan.count}")

    def report(self):
        print("\n=== discovery collector 결과 ===")
        for k, v in self.stats.items():
            print(f"  {k}: {v}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--period-days", type=int, default=30)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--status", action="store_true")
    args = p.parse_args()

    c = DiscoveryCollector(dry_run=args.dry_run)
    if args.status:
        c.show_status()
        return

    if args.tier == 1:
        print(f"Tier 1: loanItemSrch × {len(KDC_BUCKETS)} KDC × {args.pages} pages × {PAGE_SIZE}/page")
        rows = c.fetch_tier1(args.period_days, args.pages)
        c.filter_and_upsert(rows)
    else:
        print(f"Tier {args.tier} 는 다음 task 에서 구현됩니다.")

    c.report()


if __name__ == "__main__":
    main()
