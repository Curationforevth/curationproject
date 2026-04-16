"""Strategy C backfill: 기존 books.loan_count 를 usageAnalysisList 기준으로 통일.

Spec: docs/superpowers/specs/2026-04-16-data4library-aladin-hybrid-collection.md

동작:
  - `loan_count_source IS NULL OR != 'usageAnalysisList'` 인 책을 대상
  - 각 ISBN 에 usageAnalysisList 호출 → book.loanCnt + loan_count_12mo 추출
  - books 테이블 UPDATE (loan_count, loan_count_12mo, loan_count_source,
    loan_count_updated_at, library_keywords, related_isbns.co_loan)
  - Idempotent: 재실행 시 이미 처리된 책은 자동 제외 (loan_count_source 필터)

예상 소요: ~2,700권 × 0.3s = ~15분.

사용법:
  python3 scripts/backfill_loan_count_unify.py --dry-run     # 시뮬레이션
  python3 scripts/backfill_loan_count_unify.py               # 전체
  python3 scripts/backfill_loan_count_unify.py --limit 100   # 100권만
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
load_dotenv(os.path.join(REPO, ".env"))

from scripts.lib.data4library_api import (  # noqa: E402
    fetch_usage_analysis, parse_usage_analysis,
)

REQUEST_DELAY = 0.3


class Backfiller:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key = None
        self._sb = None
        self.stats = {
            "fetched": 0, "updated": 0,
            "skipped_empty": 0, "errors": 0,
        }

    @property
    def api_key(self):
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

    def fetch_candidates(self, limit: int | None = None) -> list[dict]:
        """loan_count_source 가 usageAnalysisList 가 아닌 책 전수 조회."""
        all_books: list[dict] = []
        offset = 0
        page_size = 1000
        while True:
            q = (
                self.sb.table("books")
                .select("id, isbn, title")
                .not_.is_("isbn", "null")
            )
            # OR(loan_count_source IS NULL, loan_count_source != 'usageAnalysisList')
            # supabase-py 는 OR 을 or_ 필터로 표현. 여기선 간단히 NOT equal + NULL 허용.
            # 두 조건 합치기: .or_("loan_count_source.is.null,loan_count_source.neq.usageAnalysisList")
            q = q.or_("loan_count_source.is.null,loan_count_source.neq.usageAnalysisList")
            res = q.range(offset, offset + page_size - 1).execute()
            rows = res.data or []
            all_books.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
            if limit and len(all_books) >= limit:
                break
        if limit:
            return all_books[:limit]
        return all_books

    def update_one(self, book_id: str, usage: dict):
        if self.dry_run:
            return
        payload = {
            "loan_count": usage.get("loan_count") or 0,
            "loan_count_12mo": usage.get("loan_count_12mo") or 0,
            "loan_count_source": "usageAnalysisList",
            "loan_count_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if usage.get("library_keywords"):
            payload["library_keywords"] = usage["library_keywords"]
        if usage.get("co_loan_isbns"):
            payload["related_isbns"] = {"co_loan": usage["co_loan_isbns"]}
        self.sb.table("books").update(payload).eq("id", book_id).execute()

    def run(self, limit: int | None = None) -> int:
        print("📚 backfill 대상 조회 중...")
        candidates = self.fetch_candidates(limit)
        total = len(candidates)
        print(f"   대상 {total}권\n")
        if total == 0:
            print("✅ 모든 책이 이미 usageAnalysisList 기준으로 통일되어 있음.")
            return 0

        for i, book in enumerate(candidates, 1):
            isbn = book.get("isbn")
            if not isbn:
                continue
            try:
                raw = fetch_usage_analysis(self.api_key, isbn, timeout=15.0)
                usage = parse_usage_analysis(raw)
                self.stats["fetched"] += 1
                if usage.get("is_empty"):
                    self.stats["skipped_empty"] += 1
                self.update_one(book["id"], usage)
                self.stats["updated"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({isbn}): {e}")

            if i % 50 == 0 or i <= 3:
                prefix = "(dry-run) " if self.dry_run else ""
                print(f"  {prefix}{i}/{total} | "
                      f"updated={self.stats['updated']} "
                      f"empty={self.stats['skipped_empty']} "
                      f"errors={self.stats['errors']}")

            time.sleep(REQUEST_DELAY)

        print(f"\n{'=' * 50}")
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"{prefix}backfill 완료")
        print(f"{'=' * 50}")
        for k, v in self.stats.items():
            print(f"  {k}: {v}")
        return 1 if self.stats["errors"] > 0 else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    b = Backfiller(dry_run=args.dry_run)
    return b.run(limit=args.limit) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
