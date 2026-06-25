"""Daily loan_count 순환 갱신 — 기존 책의 loan_count/loan_count_12mo 를 최신화.

loan_count_updated_at 이 가장 오래된 순으로 N권씩 usageAnalysisList 재호출.
전체 DB 를 ~14일 주기로 순환 (200권/일 × 2,700권).

사용법:
  python3 scripts/refresh_loan_count.py --dry-run --limit 5   # 대상만 확인
  python3 scripts/refresh_loan_count.py --limit 200           # 실행
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

import requests
from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
load_dotenv(os.path.join(REPO, ".env"))

from scripts.lib.data4library_api import (  # noqa: E402
    fetch_usage_analysis, parse_usage_analysis,
)
from scripts.lib.books_upsert import update_loan_count_by_book_id  # noqa: E402

REQUEST_DELAY = 0.3
MAX_CONSECUTIVE_ERRORS = 10

# usageAnalysisList 는 최重 엔드포인트 → 60s. (15s 는 잦은 Read timeout 원인이었음)
USAGE_TIMEOUT = 60.0
# 두 실패 양상 구분 (backfill_loan_count_unify 와 동일 정책):
#  - 빈 응답(RuntimeError): 미수록 ISBN(신간 979-11-…)은 거의 영구. EMPTY_RETRIES 만큼만
#    재확인 후 no_data(loan_count=0) 확정 → update 로 updated_at stamp → 큐 뒤로 밀려
#    매 run 재호출 멈춤(다음 사이클 ~14일 뒤 재확인). 에러 아님.
#  - timeout/connection(RequestException): 진짜 transient → backoff 재시도, 끝까지
#    실패하면 raise → error 카운트 + updated_at 미stamp(NULL 유지 → 다음 run 재시도).
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
EMPTY_RETRIES = 1
EMPTY_RETRY_DELAY = 0.5


class LoanCountRefresher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key = None
        self._sb = None
        self.stats = {
            "fetched": 0, "updated": 0,
            "skipped_empty": 0, "no_data": 0,
            "retried": 0, "errors": 0,
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

    def fetch_stale(self, limit: int = 200) -> list[dict]:
        """loan_count_updated_at 가장 오래된 순 (NULL 먼저) 으로 limit 건 조회."""
        res = (
            self.sb.table("books")
            .select("id, isbn, title")
            .not_.is_("isbn", "null")
            .order("loan_count_updated_at", nullsfirst=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def _fetch_usage_with_retry(self, isbn: str) -> dict:
        """usageAnalysisList + 재시도. 빈응답은 no_data 로 확정(미raise),
        진짜 transient 만 backoff 후 raise. backfill_loan_count_unify 와 동일 정책."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = fetch_usage_analysis(self.api_key, isbn, timeout=USAGE_TIMEOUT)
                usage = parse_usage_analysis(raw)
                if attempt > 0:
                    self.stats["retried"] += 1
                return usage
            except RuntimeError:
                # 빈 응답: 미수록(신간) → EMPTY_RETRIES 만큼만 재확인 후 no_data 확정.
                if attempt >= EMPTY_RETRIES:
                    self.stats["no_data"] += 1
                    return parse_usage_analysis(None)
                time.sleep(EMPTY_RETRY_DELAY)
            except requests.exceptions.RequestException as e:
                last_exc = e
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.4))
        assert last_exc is not None
        raise last_exc

    def refresh_one(self, book: dict) -> str:
        """1권 갱신. Returns: 'updated' | 'no_data' | 'error'.

        no_data(빈응답/미수록)도 updated_at 을 stamp 한다 — 그래야 큐 뒤로 밀려
        매 run 재호출되지 않는다([[feedback_accumulate_not_realtime_api]]).
        transient error 만 stamp 하지 않아 다음 run 에서 재시도된다.
        """
        isbn = book.get("isbn")
        try:
            usage = self._fetch_usage_with_retry(isbn)
        except Exception as e:
            self.stats["errors"] += 1
            if self.stats["errors"] <= 5:
                print(f"  ✗ transient 에러 ({isbn}): {e}")
            return "error"

        self.stats["fetched"] += 1
        is_empty = usage.get("is_empty")
        if is_empty:
            self.stats["skipped_empty"] += 1

        if self.dry_run:
            return "no_data" if is_empty else "updated"

        extra = {}
        if usage.get("library_keywords"):
            extra["library_keywords"] = usage["library_keywords"]
        if usage.get("co_loan_isbns"):
            extra["related_isbns"] = {"co_loan": usage["co_loan_isbns"]}

        # no_data 여도 update — loan_count=0, updated_at stamp 로 재호출 방지.
        update_loan_count_by_book_id(
            self.sb, book["id"],
            usage.get("loan_count") or 0,
            usage.get("loan_count_12mo") or 0,
            extra=extra or None,
        )
        self.stats["updated"] += 1
        return "no_data" if is_empty else "updated"

    def run(self, limit: int = 200) -> int:
        print("🔄 refresh 대상 조회 중...")
        candidates = self.fetch_stale(limit)
        total = len(candidates)
        print(f"   대상 {total}권\n")
        if total == 0:
            print("✅ 갱신 대상 없음.")
            return 0

        consecutive_errors = 0
        early_stop = False
        for i, book in enumerate(candidates, 1):
            result = self.refresh_one(book)

            if result == "error":
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"\n⛔ 연속 {MAX_CONSECUTIVE_ERRORS}회 에러 — API 장애 판단, 조기 중단.")
                    early_stop = True
                    break
            else:
                consecutive_errors = 0

            if i % 50 == 0 or i <= 3:
                prefix = "(dry-run) " if self.dry_run else ""
                print(f"  {prefix}{i}/{total} | "
                      f"updated={self.stats['updated']} "
                      f"empty={self.stats['skipped_empty']} "
                      f"errors={self.stats['errors']}")

            time.sleep(REQUEST_DELAY)

        print(f"\n{'=' * 50}")
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"{prefix}refresh 완료")
        print(f"{'=' * 50}")
        for k, v in self.stats.items():
            print(f"  {k}: {v}")
        # no_data(미수록)·산발적 transient 는 정상 — 빈응답을 매 run 재호출하지
        # 않도록 stamp 했고, transient 는 다음 run 재시도된다. job 은 API 가 통째로
        # 죽었을 때(연속 에러 조기중단)만 실패로 본다.
        return 1 if early_stop else 0


def main():
    p = argparse.ArgumentParser(
        description="Daily loan_count 순환 갱신",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="API 호출은 하되 DB 쓰기 생략")
    p.add_argument("--limit", type=int, default=200,
                   help="갱신할 최대 권수 (기본 200)")
    args = p.parse_args()

    r = LoanCountRefresher(dry_run=args.dry_run)
    return r.run(limit=args.limit)


if __name__ == "__main__":
    sys.exit(main() or 0)
