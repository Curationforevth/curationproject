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
import random
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
load_dotenv(os.path.join(REPO, ".env"))

from scripts.lib.data4library_api import (  # noqa: E402
    fetch_usage_analysis, parse_usage_analysis,
)

REQUEST_DELAY = 0.3

# usageAnalysisList 는 누적+월별+키워드+동시대출을 한 번에 내려주는 가장 무거운
# 엔드포인트라 다른 엔드포인트(60s)와 동일하게 60s 로 둔다. 기존 15s 는 잦은
# Read timeout 의 직접 원인이었음.
USAGE_TIMEOUT = 60.0
# fetch_usage_analysis 는 빈 응답을 RuntimeError(재시도 유도)로, requests 는
# timeout/connection 을 RequestException 으로 raise. 일회성 backfill 은 discovery/
# collector 와 달리 '다음 run' 재시도가 없으므로 inline 재시도가 필수.
#
# timeout/connection 은 진짜 transient → MAX_RETRIES 만큼 backoff 재시도.
# 빈 응답은 거의 영구(신간 미수록, 같은 ISBN 항상 빈 body) → 1회만 재확인 후
# no-data 확정. (빈 응답을 매번 3회 재시도하면 신간 수천 권 × 백오프 = 수 시간.)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
EMPTY_RETRIES = 1
EMPTY_RETRY_DELAY = 0.5


class Backfiller:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key = None
        self._sb = None
        self.stats = {
            "fetched": 0, "updated": 0,
            "skipped_empty": 0, "errors": 0, "retried": 0,
            "no_data": 0,
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

    def fetch_candidates(self, limit: int | None = None,
                         min_loan_count: int | None = None) -> list[dict]:
        """loan_count_source 가 usageAnalysisList 가 아닌 책 조회.

        min_loan_count: 지정 시 loan_count >= N 인 책만 (data4library 수록 가능성이
          높은 고가치 책 우선 타겟. loan_count 0/NULL 신간은 대부분 미수록이라
          backfill 해도 no_data 라 Strategy C 에 기여 없음 → 선택적으로 제외).
        """
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
            if min_loan_count is not None:
                q = q.gte("loan_count", min_loan_count)
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

    def _fetch_usage_with_retry(self, isbn: str) -> dict:
        """usageAnalysisList 호출 + 재시도. 두 실패 양상을 구분 처리.

        - RuntimeError(빈 응답): data4library 는 자기 대출 데이터에 없는 ISBN(주로
          신간 979-11-9x)에 HTTP 200 + 빈 body 를 반환한다(영구). 도크스트링의
          "빈응답=transient" 가정은 신간엔 틀림 → 몇 번 재시도해도 비면 '미수록'으로
          확정하고 '데이터 없음'(loan_count=0)을 반환한다. 에러가 아니라 정상 결과:
          신간은 실제 도서관 대출이 0이므로 0 마킹이 정확하고, loan_count_source 가
          채워져 매 run 무한 재시도도 멈춘다(이후 daily loan_count cron 이 재확인).
        - RequestException(Read timeout / connection): 진짜 transient → backoff
          재시도, 끝까지 실패하면 raise(상위에서 errors 카운트, source NULL 유지 →
          다음 run 에서 재시도). usageAnalysisList 는 최重 엔드포인트라 USAGE_TIMEOUT(60s).
        """
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = fetch_usage_analysis(self.api_key, isbn, timeout=USAGE_TIMEOUT)
                usage = parse_usage_analysis(raw)
                if attempt > 0:
                    self.stats["retried"] += 1
                return usage
            except RuntimeError:
                # 빈 응답: EMPTY_RETRIES 만큼만 재확인 후 미수록(신간) 확정 → 에러 아님.
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

    def run(self, limit: int | None = None,
            min_loan_count: int | None = None) -> int:
        print("📚 backfill 대상 조회 중...")
        candidates = self.fetch_candidates(limit, min_loan_count=min_loan_count)
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
                usage = self._fetch_usage_with_retry(isbn)
                self.stats["fetched"] += 1
                if usage.get("is_empty"):
                    self.stats["skipped_empty"] += 1
                self.update_one(book["id"], usage)
                self.stats["updated"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({isbn}, {MAX_RETRIES}회 재시도 후): {e}")

            if i % 50 == 0 or i <= 3:
                prefix = "(dry-run) " if self.dry_run else ""
                print(f"  {prefix}{i}/{total} | "
                      f"updated={self.stats['updated']} "
                      f"no_data={self.stats['no_data']} "
                      f"retried={self.stats['retried']} "
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
    p.add_argument("--min-loan-count", type=int, default=None,
                   help="loan_count >= N 인 책만 (data4library 수록 가능성 높은 "
                        "고가치 책 우선; 0/NULL 신간 제외). 미지정 시 전수.")
    args = p.parse_args()

    b = Backfiller(dry_run=args.dry_run)
    return b.run(limit=args.limit, min_loan_count=args.min_loan_count) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
