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
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scripts.lib.data4library_api import (
    fetch_loan_item_page,
    fetch_recommand,
    fetch_monthly_keywords,
    fetch_usage_analysis,
    parse_monthly_keywords,
    parse_usage_analysis,
    fetch_search,
    parse_book_docs,
    is_adult_general,
)
from scripts.lib.dedup_checker import DeduplicateChecker, DedupAction
from scripts.lib.book_filter import is_non_book
from scripts.lib.books_upsert import (
    upsert_books_rich_merge,
    update_loan_count_by_book_id,
)
from scripts.lib.state_manager import StateManager

load_dotenv(os.path.join(REPO, ".env"))


PAGE_SIZE = 50
REQUEST_DELAY = 0.3  # API 응답 자체가 ~0.4s이므로 실제 간격 ~0.7s

# ── 회복력 파라미터 (2026-08-26) ──────────────────────────────────────────
# 정보나루는 GitHub Actions 러너(해외 IP)에서 상습적으로 Read timeout 난다
# (같은 ISBN 이 한국 IP 에선 평균 1.82s). 아래 3개가 "느린 경로에서도 수집은
# 계속된다" 를 보장한다 — 상세 배경은 scripts/tests/test_discovery_resilience.py.
#
#  USAGE_FAIL_STREAK_LIMIT: 연속 N회 실패하면 이번 run 은 usageAnalysisList 를
#    포기(회로 개방)하고 남은 책을 loan_count=NULL 로 저장한다. 경로가 죽은
#    상태에서 매 책마다 15s 씩 태우면 25분 예산이 통째로 날아가고 저장은 0권.
#  FLUSH_EVERY: 루프 끝에 한 번만 upsert 하면 job timeout = 전량 유실.
#  DISCOVERY_BUDGET_SECONDS: job timeout 보다 먼저 스스로 멈추고 flush 한다.
USAGE_FAIL_STREAK_LIMIT = int(os.getenv("DISCOVERY_USAGE_FAIL_STREAK", "5"))
FLUSH_EVERY = int(os.getenv("DISCOVERY_FLUSH_EVERY", "50"))
DEFAULT_BUDGET_SECONDS = float(os.getenv("DISCOVERY_BUDGET_SECONDS", "0") or 0)


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


def select_seed_isbns_for_tier2(rows: list[dict], top_n: int = 50) -> list[str]:
    """Select top-N ISBN seeds for Tier 2 recommandList expansion.

    Sort by loan_count desc, take ISBN13s, drop blanks, dedupe in order.
    """
    sorted_rows = sorted(rows, key=lambda r: r.get("loan_count") or 0, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for r in sorted_rows:
        isbn = (r.get("isbn13") or "").strip()
        if not isbn or isbn in seen:
            continue
        seen.add(isbn)
        out.append(isbn)
        if len(out) >= top_n:
            break
    return out


def filter_single_token_keywords(keywords: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep only single-word keywords with len >= 2 (srchBooks limitation).

    Verified: srchBooks fails on multi-token keywords like '소년이 온다'.
    Dedupes by word (keeps first occurrence).
    """
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for word, weight in keywords:
        if not word or len(word) < 2 or " " in word:
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append((word, weight))
    return out


def trigger_enrich_pipeline(dry_run: bool = False, limit: Optional[int] = None) -> int:
    """Discovery 수집 직후 pipeline_orchestrator 를 subprocess 로 호출.

    Returns the orchestrator's exit code (0 = success).
    """
    cmd = ["python3", "scripts/pipeline_orchestrator.py"]
    if dry_run:
        cmd.append("--dry-run")
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    print(f"\n▶ enrich pipeline 트리거: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=REPO, check=False)
    return proc.returncode


def sanitize_for_upsert(parsed: dict) -> dict:
    """Convert a parsed loanItem dict to a books-table row.

    loan_count 는 loanItemSrch 가 반환한 기간별 값을 임시 저장. 이후 usageAnalysisList
    후처리 (Strategy C) 에서 book.loanCnt (누적) + loan_count_12mo 로 덮어써짐.
    sales_point 는 알라딘 전용 → 여기서 건드리지 않음 (2026-04-16 버그 fix).

    loan_count=None(모름) 은 None 그대로 둔다 — 0 으로 위조하면 "대출 0회로
    확인됨" 이라는 거짓이 축적되고, loan_count_updated_at 도 NULL 로 남아야
    refresh_loan_count(nullsfirst) 가 우선 채운다. 미수록 확정(빈 응답)은 0.
    """
    return {
        "isbn": parsed["isbn13"],
        "title": parsed.get("title") or "",
        "author": extract_first_author(parsed.get("author_raw")),
        "publisher": parsed.get("publisher"),
        "cover_url": parsed.get("cover_url"),
        "loan_count": parsed.get("loan_count"),
        "source": "data4library",
    }


class DiscoveryCollector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._api_key: Optional[str] = None
        self._sb = None
        self._dedup: Optional[DeduplicateChecker] = None
        # 회복력 상태 — 연속 실패 회로 + 시간 예산
        self._usage_fail_streak = 0
        self._usage_circuit_open = False
        self._deadline: Optional[float] = None
        self.stats = {
            "fetched_raw": 0,
            "filtered_children": 0,
            "filtered_non_book": 0,
            "filtered_isbn_dup": 0,
            "filtered_edition_dup": 0,
            "usage_api_errors": 0,
            "usage_no_data": 0,
            # (구 skipped_usage_fail 제거 — transient 실패는 더 이상 row 를 버리지
            #  않고 usage_unknown_saved 로 저장된다)
            "usage_unknown_saved": 0,     # loan_count 모름으로 저장(크론이 채움)
            "usage_circuit_skipped": 0,   # 회로 개방 후 호출 자체를 건너뜀
            "budget_exhausted": 0,        # 예산 소진으로 중단(1=중단됨)
            "updated_existing_loan_count": 0,
            "upserted": 0,
            "errors": 0,
        }

    # ── 시간 예산 ────────────────────────────────────────────────────────
    def set_budget(self, seconds: Optional[float]):
        """이 run 이 쓸 수 있는 최대 시간. job timeout 보다 짧게 잡아야
        강제 종료(전량 유실) 대신 스스로 멈추고 flush 할 수 있다."""
        self._deadline = (time.monotonic() + seconds) if seconds else None

    def _budget_exhausted(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

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

    def fetch_tier2_seeds_from_db(self, top_n: int) -> list[str]:
        """Tier 2 시드 ISBN 을 books 테이블의 loan_count desc top-N 에서 가져온다.

        과거에는 fetch_tier1() 를 다시 돌려서 시드를 얻었지만, 이미 Tier 1
        결과가 books 에 upsert 되어 있으므로 DB 에서 직접 읽으면 됨.
        정보나루 API 호출 10 KDC × pages 만큼 절약 (KI-004).
        """
        result = (
            self.sb.table("books")
            .select("isbn")
            .not_.is_("isbn", "null")
            .not_.is_("loan_count", "null")
            .order("loan_count", desc=True)
            .limit(top_n)
            .execute()
        )
        seeds = [r["isbn"] for r in (result.data or []) if r.get("isbn")]
        return seeds

    def fetch_tier2(self, seed_isbns: list[str]) -> list[dict]:
        """Tier 2: recommandList for each seed ISBN."""
        all_rows: list[dict] = []
        for i, isbn in enumerate(seed_isbns):
            try:
                raw = fetch_recommand(api_key=self.api_key, isbn13=isbn, page_size=10)
                parsed = parse_book_docs(raw)
                all_rows.extend(parsed)
                self.stats["fetched_raw"] += len(parsed)
                if (i + 1) % 10 == 0:
                    print(f"  recommandList progress: {i+1}/{len(seed_isbns)}")
            except Exception as e:
                print(f"  ✗ recommandList isbn={isbn}: {e}")
                self.stats["errors"] += 1
            time.sleep(REQUEST_DELAY)
        return all_rows

    def fetch_tier3(self, month: str) -> list[dict]:
        """Tier 3: monthlyKeywords -> filter single tokens -> srchBooks."""
        try:
            kw_raw = fetch_monthly_keywords(api_key=self.api_key, month=month)
        except Exception as e:
            print(f"  ✗ monthlyKeywords {month}: {e}")
            self.stats["errors"] += 1
            return []
        keywords = parse_monthly_keywords(kw_raw)
        single = filter_single_token_keywords(keywords)
        print(f"  monthlyKeywords {month}: {len(keywords)} 키워드 → 단일 토큰 {len(single)}")

        all_rows: list[dict] = []
        for i, (word, _weight) in enumerate(single):
            try:
                raw = fetch_search(api_key=self.api_key, keyword=word, page_size=10)
                parsed = parse_book_docs(raw)
                all_rows.extend(parsed)
                self.stats["fetched_raw"] += len(parsed)
                if (i + 1) % 20 == 0:
                    print(f"  srchBooks progress: {i+1}/{len(single)}")
            except Exception as e:
                print(f"  ✗ srchBooks '{word}': {e}")
                self.stats["errors"] += 1
            time.sleep(REQUEST_DELAY)
        return all_rows

    def _fetch_accurate_loan_count(self, isbn: str) -> Optional[dict]:
        """usageAnalysisList 호출 → {loan_count, loan_count_12mo, ...} 반환.

        두 실패 양상을 구분한다([[feedback_accumulate_not_realtime_api]]):
          - 빈 응답(RuntimeError): 미수록 ISBN(신간 979-11-…)은 HTTP 200+빈 body 를
            영구 반환. None(skip) 하면 DB 미저장 → 다음 run 또 '신규' 로 보여 재호출
            (무한). no_data(loan_count=0) 로 확정·반환해 **저장**하면 dedup index 에
            올라 다음 run SKIP → 재호출 멈춤. (loan_count 0 은 ~14일 주기 loan_count
            cron 이 재확인. Strategy C 상 loanItemSrch 기간값은 저장 안 함.)
          - timeout/connection(RequestException): 진짜 transient → None.
            호출측이 loan_count '모름'(NULL)으로 **저장**하고 refresh 크론에 넘긴다.

        연속 USAGE_FAIL_STREAK_LIMIT 회 실패하면 회로를 열어 남은 호출을 아예
        건너뛴다 — 러너→정보나루 경로가 죽은 run 에서 책당 15s 씩 태우다 job
        timeout 으로 전량 유실되던 것이 8월 25/25 실패의 직접 원인이었다.
        """
        if self._usage_circuit_open:
            self.stats["usage_circuit_skipped"] += 1
            return None
        try:
            raw = fetch_usage_analysis(self.api_key, isbn, timeout=15.0)
            parsed = parse_usage_analysis(raw)
            self._usage_fail_streak = 0
            return parsed
        except RuntimeError:
            # 빈 응답 = 미수록 확정 → no_data 로 저장(축적). 서버가 응답은 했으므로
            # 경로는 살아있다 → 연속 실패 카운터 리셋.
            self._usage_fail_streak = 0
            self.stats["usage_no_data"] += 1
            return parse_usage_analysis(None)
        except requests.exceptions.RequestException as e:
            self.stats["usage_api_errors"] += 1
            self._usage_fail_streak += 1
            if self.stats["usage_api_errors"] <= 5:
                print(f"  ✗ usageAnalysisList transient ({isbn}): {e}")
            if self._usage_fail_streak >= USAGE_FAIL_STREAK_LIMIT:
                self._usage_circuit_open = True
                print(f"  ⚡ usageAnalysisList 연속 {self._usage_fail_streak}회 실패 "
                      f"→ 이번 run 은 호출 중단, loan_count 는 refresh 크론에 위임")
            return None

    def filter_and_upsert(self, parsed_rows: list[dict]) -> int:
        """Strategy C 흐름:
          1) children / non-book / ISBN 배치 dedup 필터 (기존)
          2) 각 row 에 usageAnalysisList 호출 → 정확한 loan_count/loan_count_12mo 확보
          3) dedup_checker.check() 로 NEW / SKIP / UPDATE_LOAN_COUNT 판정
             - NEW: upsert 대상 리스트에 추가
             - UPDATE_LOAN_COUNT: 즉시 기존 row 의 loan_count 만 UPDATE
             - SKIP: 버림
          4) NEW 리스트를 upsert_books_rich_merge 로 최종 저장
        """
        adult_rows = [r for r in parsed_rows if is_adult_general(r)]
        self.stats["filtered_children"] += len(parsed_rows) - len(adult_rows)
        print(f"  성인 단행본 필터: {len(adult_rows)}/{len(parsed_rows)}")

        # B5: smart_batch 와 동일한 non-book 필터 적용 (문제집/수험서/만화 등).
        book_rows = [
            r for r in adult_rows
            if not is_non_book({
                "title": r.get("title") or "",
                "categoryName": r.get("class_name") or "",
            })
        ]
        self.stats["filtered_non_book"] += (len(adult_rows) - len(book_rows))
        print(f"  non-book 필터: {len(book_rows)}/{len(adult_rows)}")

        by_isbn = dedup_in_batch_by_isbn(book_rows)
        self.stats["filtered_isbn_dup"] += len(book_rows) - len(by_isbn)
        print(f"  배치 ISBN dedup: {len(by_isbn)}/{len(book_rows)}")

        if not by_isbn:
            return 0

        # Strategy C 핵심: 각 row 에 usageAnalysisList 호출 + dedup_checker 분기
        new_rows: list[dict] = []
        total_upserted = 0
        for idx, r in enumerate(by_isbn):
            # 예산 소진 → 남은 row 를 붙들지 않고 중단(아래에서 flush).
            # job timeout 으로 강제 종료되면 그때까지 처리분이 통째로 날아간다.
            # 잔여 건수는 루프 인덱스 기준 — new_rows 는 flush 마다 비워지므로
            # 그걸로 세면 틀린다.
            if self._budget_exhausted():
                self.stats["budget_exhausted"] = 1
                print(f"  ⏱ 시간 예산 소진 → {idx}/{len(by_isbn)} 처리 후 중단 "
                      f"(남은 {len(by_isbn) - idx}건은 다음 run)")
                break

            title = r.get("title") or ""
            author = extract_first_author(r.get("author_raw"))
            isbn = r["isbn13"]

            # usageAnalysisList — 정확한 loan_count 확보. Strategy C 원칙상
            # loan_count 는 usageAnalysisList 누적값으로만 통일한다. loanItemSrch
            # 기간값은 dedup 비교/저장에 쓰지 않는다 (소스 오염 방지).
            if self.dry_run:
                # dry-run: API 호출 없이 loanItemSrch 추정값으로 흐름만 검증
                usage = None
                accurate_loan_count = r.get("loan_count") or 0
                accurate_loan_12mo = 0
            else:
                usage = self._fetch_accurate_loan_count(isbn)
                if not self._usage_circuit_open:
                    time.sleep(REQUEST_DELAY)
                if usage is None:
                    # transient 실패(또는 회로 개방) → **책은 저장하고** loan_count 만
                    # 모름(NULL)으로 남긴다. refresh_loan_count 가 nullsfirst 로
                    # 우선 채운다. 예전엔 여기서 continue 해 row 를 통째로 버렸고,
                    # 러너 경로가 느린 8월 내내 신규 유입이 0권이 됐다.
                    accurate_loan_count = None
                    accurate_loan_12mo = None
                else:
                    accurate_loan_count = usage.get("loan_count") or 0  # 0 도 유효한 누적값
                    accurate_loan_12mo = usage.get("loan_count_12mo") or 0

            # dedup 판정 — loan_count 를 모르면 0 으로 비교(보수적).
            # 기존 에디션을 밀어내지(UPDATE) 않고, 같은 작품이 이미 있으면 SKIP 된다.
            action, existing_book_id = self.dedup.check(
                title, author, isbn,
                0 if accurate_loan_count is None else accurate_loan_count,
            )
            if action == DedupAction.SKIP:
                self.stats["filtered_edition_dup"] += 1
                continue
            if action == DedupAction.UPDATE_LOAN_COUNT and existing_book_id:
                if not self.dry_run:
                    try:
                        extra = {}
                        if usage and usage.get("library_keywords"):
                            extra["library_keywords"] = usage["library_keywords"]
                        if usage and usage.get("co_loan_isbns"):
                            extra["related_isbns"] = {"co_loan": usage["co_loan_isbns"]}
                        update_loan_count_by_book_id(
                            self.sb, existing_book_id,
                            loan_count=accurate_loan_count,
                            loan_count_12mo=accurate_loan_12mo,
                            extra=extra or None,
                        )
                        self.dedup.update_loan_count(existing_book_id, accurate_loan_count)
                        self.stats["updated_existing_loan_count"] += 1
                    except Exception as e:
                        self.stats["errors"] += 1
                        print(f"  ✗ UPDATE loan_count ({isbn} → {existing_book_id}): {e}")
                continue

            # NEW — 새 row. loan_count 를 usageAnalysisList 기준으로 교체
            # (모르면 None → NULL 저장, _usage=None 이라 loan_count_updated_at 도
            #  안 찍혀 refresh 크론이 nullsfirst 로 먼저 집어간다).
            r["loan_count"] = accurate_loan_count
            r["_usage"] = usage  # Strategy C 필드 저장용 보관
            if accurate_loan_count is None:
                self.stats["usage_unknown_saved"] += 1
            new_rows.append(r)
            if not self.dry_run:
                self.dedup.register(title, author, isbn,
                                    book_id=None, loan_count=accurate_loan_count)

            # 중간 flush — job 이 강제 종료돼도 여기까지는 DB 에 남는다.
            if not self.dry_run and len(new_rows) >= FLUSH_EVERY:
                total_upserted += self._flush_new_rows(new_rows)
                new_rows = []

        print(f"  Strategy C dedup: NEW {total_upserted + len(new_rows)} / "
              f"UPDATE {self.stats['updated_existing_loan_count']} / "
              f"SKIP {self.stats['filtered_edition_dup']}")

        if self.dry_run:
            rows = [sanitize_for_upsert(r) for r in new_rows]
            if not rows:
                return 0
            print(f"  (dry-run) would upsert {len(rows)} rows")
            print(f"  sample: {rows[0]}")
            return len(rows)

        total_upserted += self._flush_new_rows(new_rows)
        return total_upserted

    def _flush_new_rows(self, new_rows: list[dict]) -> int:
        """모아둔 NEW row 를 즉시 저장한다(부분 진행 보존)."""
        if not new_rows:
            return 0
        rows = [sanitize_for_upsert(r) for r in new_rows]
        # B6: field-level richer merge (cross-source overwrite 방지 + richer 선택).
        upserted = upsert_books_rich_merge(self.sb, rows, chunk_size=200)
        self.stats["upserted"] += upserted

        # Strategy C: 갓 upsert된 NEW 책들에 대해 loan_count_12mo / source / updated_at
        # 을 별도 UPDATE. upsert_books_rich_merge 는 해당 필드를 모르므로 이 경로 필요.
        # _usage 가 None(모름)인 row 는 건너뛴다 → loan_count_updated_at NULL 유지.
        self._apply_usage_fields(new_rows)
        return upserted

    def _apply_usage_fields(self, new_rows: list[dict]):
        """NEW 로 upsert된 row 들에 Strategy C 필드 (loan_count_12mo, source,
        library_keywords, related_isbns.co_loan) 적용.
        """
        if not new_rows:
            return
        # ISBN 으로 book_id 조회
        isbns = [r["isbn13"] for r in new_rows if r.get("isbn13")]
        if not isbns:
            return
        res = (
            self.sb.table("books")
            .select("id, isbn")
            .in_("isbn", isbns)
            .execute()
        )
        id_by_isbn = {row["isbn"]: row["id"] for row in (res.data or [])}

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in new_rows:
            usage = r.get("_usage")
            if not usage:
                continue
            book_id = id_by_isbn.get(r.get("isbn13"))
            if not book_id:
                continue
            payload = {
                "loan_count_12mo": usage.get("loan_count_12mo") or 0,
                "loan_count_source": "usageAnalysisList",
                "loan_count_updated_at": now_iso,
            }
            if usage.get("library_keywords"):
                payload["library_keywords"] = usage["library_keywords"]
            if usage.get("co_loan_isbns"):
                payload["related_isbns"] = {"co_loan": usage["co_loan_isbns"]}
            try:
                self.sb.table("books").update(payload).eq("id", book_id).execute()
            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ Strategy C 필드 적용 실패 ({r.get('isbn13')}): {e}")

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
    p.add_argument("--tier2-seeds", type=int, default=50,
                   help="Tier 2: how many top seed ISBNs to expand via recommandList")
    p.add_argument("--month", type=str, default=None,
                   help="Tier 3 month (YYYY-MM). Default = previous month")
    p.add_argument("--status", action="store_true")
    p.add_argument("--with-enrich", action="store_true",
                   help="수집 완료 후 pipeline_orchestrator 자동 트리거")
    p.add_argument("--enrich-limit", type=int, default=None,
                   help="--with-enrich 시 각 enrich step 에 전달할 limit "
                        "(생략 = 전체 backlog 처리)")
    p.add_argument("--budget-seconds", type=float, default=None,
                   help="이 run 의 최대 수행 시간. 초과 시 스스로 멈추고 flush "
                        "한다(job timeout 강제종료 = 전량 유실 방지). "
                        "생략 시 env DISCOVERY_BUDGET_SECONDS, 그것도 없으면 무제한")
    args = p.parse_args()

    c = DiscoveryCollector(dry_run=args.dry_run)
    budget = args.budget_seconds if args.budget_seconds is not None else DEFAULT_BUDGET_SECONDS
    if budget:
        print(f"⏱ 시간 예산 {budget:.0f}s")
        c.set_budget(budget)
    if args.status:
        c.show_status()
        return 0

    if args.tier == 1:
        print(f"Tier 1: loanItemSrch × {len(KDC_BUCKETS)} KDC × {args.pages} pages × {PAGE_SIZE}/page")
        rows = c.fetch_tier1(args.period_days, args.pages)
        c.filter_and_upsert(rows)
    elif args.tier == 2:
        print(f"Tier 2: recommandList for top-{args.tier2_seeds} books from books DB (loan_count desc)")
        seeds = c.fetch_tier2_seeds_from_db(top_n=args.tier2_seeds)
        print(f"  selected {len(seeds)} seed ISBNs from DB")
        if not seeds:
            print("  ⚠ DB 에 loan_count 가 채워진 책이 없습니다. Tier 1 을 먼저 실행하세요.")
            sys.exit(1)
        tier2_rows = c.fetch_tier2(seeds)
        c.filter_and_upsert(tier2_rows)
    elif args.tier == 3:
        if args.month:
            month = args.month
        else:
            today = datetime.now().date()
            mm = today.month - 1 if today.month > 1 else 12
            yyyy = today.year if today.month > 1 else today.year - 1
            month = f"{yyyy}-{mm:02d}"
        print(f"Tier 3: monthlyKeywords month={month} → srchBooks")
        rows = c.fetch_tier3(month)
        c.filter_and_upsert(rows)

    c.report()

    # F5: state_manager ��� 실행 결과 기�� (smart_batch_collector 와 통일)
    if not args.dry_run:
        source_type = f"data4library_tier{args.tier}"
        sm = StateManager(c.sb)
        sm.upsert_state(
            source_type=source_type,
            total_items_found=c.stats.get("fetched_raw", 0),
            unique_items_saved=c.stats.get("upserted", 0),
            completed=True,
        )

    # B2: stats.errors 가 있으면 exit 1. cron 이 감지 가능하도록.
    rc = 1 if c.stats.get("errors", 0) > 0 else 0

    if args.with_enrich:
        code = trigger_enrich_pipeline(dry_run=args.dry_run, limit=args.enrich_limit)
        if code != 0:
            print(f"⚠ enrich pipeline 실패 (exit {code})", file=sys.stderr)
            return max(rc, code)

    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)
