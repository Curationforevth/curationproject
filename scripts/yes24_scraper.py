"""
YES24 책 상세 텍스트 스크래퍼

books 테이블에서 rich_description이 NULL인 책을 찾아
YES24에서 책소개 + 출판사리뷰 + 책속으로를 스크래핑.

매일 배치로 돌림. 하루 ~250권 (GitHub Actions 30분 내).

사용법:
  python3 scripts/yes24_scraper.py                  # 기본 (250권)
  python3 scripts/yes24_scraper.py --limit 50       # 50권만
  python3 scripts/yes24_scraper.py --status          # 진행 현황
  python3 scripts/yes24_scraper.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install playwright supabase python-dotenv
  playwright install firefox
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Playwright는 런타임에 import (설치 안 됐을 때 --status 등은 동작하도록)
pw = None
Browser = None


def get_playwright():
    global pw
    if pw is None:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
        except ImportError:
            print("❌ playwright 패키지가 설치되어 있지 않습니다.")
            print("   pip install playwright && playwright install firefox")
            sys.exit(1)
    return pw


class Yes24Scraper:
    BATCH_SIZE = 250  # 하루 기본 처리량
    BROWSER_RESTART_EVERY = 100  # 100권마다 브라우저 재시작
    MIN_DELAY = 2.0  # 최소 대기 (초)
    MAX_DELAY = 4.0  # 최대 대기 (초)

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self.browser = None
        self.page = None
        self.stats = {
            "processed": 0,
            "success": 0,
            "search_fail": 0,
            "isbn_mismatch": 0,
            "scrape_fail": 0,
            "errors": 0,
        }

    def _start_browser(self):
        """Firefox 브라우저 시작"""
        if self.browser:
            try:
                self.browser.close()
            except:
                pass

        p = get_playwright()
        self.browser = p.firefox.launch(headless=True)
        self.page = self.browser.new_page()

    def _close_browser(self):
        if self.browser:
            try:
                self.browser.close()
            except:
                pass
            self.browser = None
            self.page = None

    def _delay(self):
        """랜덤 딜레이"""
        time.sleep(random.uniform(self.MIN_DELAY, self.MAX_DELAY))

    def fetch_books_needing_scrape(self, limit=250):
        """rich_description이 NULL인 책 조회 (페이징)"""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = self.sb.table("books") \
                .select("id, isbn, title, author") \
                .is_("rich_description", "null") \
                .not_.is_("isbn", "null") \
                .order("sales_point", desc=True) \
                .range(offset, offset + page_size - 1) \
                .execute()
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size or len(all_books) >= limit:
                break
            offset += page_size

        return all_books[:limit]

    def search_goods_id(self, title, author):
        """YES24 Firefox 검색 → data-goods-no로 goods ID 추출"""
        clean_author = re.sub(r'\s*\(.*?\)', '', author or '').strip().split(',')[0].strip()
        core_title = title.split(' - ')[0].split(' :')[0].split(' (')[0].strip()
        query = urllib.parse.quote(f"{core_title} {clean_author}")

        try:
            self.page.goto(
                f'https://www.yes24.com/Product/Search?domain=BOOK&query={query}',
                timeout=15000,
            )
            self.page.wait_for_timeout(4000)

            elements = self.page.query_selector_all('[data-goods-no]')
            if not elements:
                return None

            return elements[0].get_attribute('data-goods-no')
        except Exception:
            return None

    def verify_isbn(self, goods_id, expected_isbn):
        """상세 페이지의 JSON-LD ISBN과 DB ISBN 대조"""
        try:
            self.page.goto(
                f'https://www.yes24.com/Product/Goods/{goods_id}',
                timeout=15000,
            )
            self.page.wait_for_timeout(2000)

            content = self.page.content()
            ld_match = re.search(
                r'<script type="application/ld\+json">(.*?)</script>',
                content, re.DOTALL,
            )
            if ld_match:
                ld = json.loads(ld_match.group(1))
                page_isbn = ld.get('gtin13') or ld.get('isbn', '')
                # ISBN-13 전체 비교, fallback으로 끝 12자리 비교
                if page_isbn and expected_isbn:
                    if len(page_isbn) >= 13 and len(expected_isbn) >= 13:
                        return page_isbn[-13:] == expected_isbn[-13:]
                    return page_isbn[-12:] == expected_isbn[-12:]
            # JSON-LD 없으면 검증 스킵 (진행)
            return True
        except Exception:
            return True  # 검증 실패 시 진행

    def scrape_detail(self):
        """현재 페이지에서 텍스트 추출 (verify_isbn에서 이미 상세 페이지에 있음)"""
        sections = {}
        for sid, name in [
            ('infoset_introduce', '책소개'),
            ('infoset_pubReivew', '출판사리뷰'),
            ('infoset_inBook', '책속으로'),
        ]:
            try:
                el = self.page.query_selector(f'#{sid}')
                if el:
                    text = el.inner_text().strip()
                    # UI 텍스트 제거
                    lines = text.split('\n')
                    cleaned = '\n'.join(
                        l for l in lines
                        if l.strip() not in ('책소개', '출판사 리뷰', '책 속으로', '접기', '펼쳐보기', '더보기')
                    )
                    if len(cleaned.strip()) > 20:
                        sections[name] = cleaned.strip()
            except Exception:
                continue

        return sections

    def save_rich_description(self, book_id, sections):
        """DB에 rich_description 저장"""
        if not sections or self.dry_run:
            return

        combined = '\n\n'.join(f'[{name}]\n{text}' for name, text in sections.items())

        self.sb.table("books").update({
            "rich_description": combined,
        }).eq("id", book_id).execute()

    def run(self, limit=250):
        """메인 실행"""
        print(f"🔍 스크래핑 필요한 도서 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_scrape(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서가 스크래핑 완료됨.")
            return

        self._start_browser()

        for i, book in enumerate(books):
            # 주기적 브라우저 재시작
            if i > 0 and i % self.BROWSER_RESTART_EVERY == 0:
                print(f"\n  🔄 브라우저 재시작 ({i}권 처리 완료)")
                try:
                    self._start_browser()
                except Exception as e:
                    print(f"  ✗ 브라우저 재시작 실패: {e}")
                    self._close_browser()
                    break

            book_id = book['id']
            isbn = book['isbn']
            title = book['title']
            author = book.get('author', '')

            try:
                # 1. YES24 검색
                goods_id = self.search_goods_id(title, author)
                if not goods_id:
                    self.stats["search_fail"] += 1
                    if self.stats["search_fail"] <= 10:
                        print(f"  ✗ 검색 실패: {title[:30]}")
                    self._delay()
                    continue

                self._delay()

                # 2. ISBN 검증 + 상세 페이지 로드
                if not self.verify_isbn(goods_id, isbn):
                    self.stats["isbn_mismatch"] += 1
                    if self.stats["isbn_mismatch"] <= 5:
                        print(f"  ⚠ ISBN 불일치: {title[:30]}")
                    self._delay()
                    continue

                # 3. 텍스트 추출
                sections = self.scrape_detail()
                if not sections:
                    self.stats["scrape_fail"] += 1
                    self._delay()
                    continue

                # 4. DB 저장
                self.save_rich_description(book_id, sections)

                total_chars = sum(len(t) for t in sections.values())
                self.stats["success"] += 1
                self.stats["processed"] += 1

                if self.stats["success"] % 25 == 0 or self.stats["success"] <= 5:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{self.stats['success']}/{len(books)}: {title[:25]} — {total_chars}자")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러: {title[:25]} — {e}")

            self._delay()

        self._close_browser()
        self.print_report(len(books))

    def print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}YES24 스크래핑 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}권")
        print(f"  성공: {s['success']}권")
        print(f"  검색 실패: {s['search_fail']}권")
        print(f"  ISBN 불일치: {s['isbn_mismatch']}권")
        print(f"  스크래핑 실패: {s['scrape_fail']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = self.sb.table("books").select("id", count="exact").execute()
        has_rich = self.sb.table("books").select("id", count="exact") \
            .not_.is_("rich_description", "null").execute()

        print(f"\n{'=' * 50}")
        print("YES24 스크래핑 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  rich_description 완료: {has_rich.count}권 ({has_rich.count * 100 // total.count if total.count else 0}%)")
        print(f"  스크래핑 필요: {total.count - has_rich.count}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="YES24 책 상세 스크래퍼")
    parser.add_argument("--limit", type=int, default=250, help="최대 처리 권수")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="진행 현황")
    args = parser.parse_args()

    scraper = Yes24Scraper(dry_run=args.dry_run)

    if args.status:
        scraper.show_status()
        return

    scraper.run(limit=args.limit)


if __name__ == "__main__":
    main()
