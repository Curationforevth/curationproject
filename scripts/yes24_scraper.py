"""
YES24 책 상세 텍스트 스크래퍼 (requests + BeautifulSoup 버전)

books 테이블에서 rich_description이 NULL인 책을 찾아
YES24에서 책소개 + 출판사리뷰 + 책속으로를 스크래핑.

분산 실행: 2시간마다 80권씩 (GitHub Actions cron).

사용법:
  python3 scripts/yes24_scraper.py                  # 기본 (80권)
  python3 scripts/yes24_scraper.py --limit 50       # 50권만
  python3 scripts/yes24_scraper.py --status          # 진행 현황
  python3 scripts/yes24_scraper.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install requests beautifulsoup4 supabase python-dotenv
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    pass  # --status 등에서는 불필요

# lib.retry 는 hard dependency — silent no-op fallback 금지
# (과거: 패스 문제로 retry 가 no-op 되어 수백 권 drop 하고도 exit 0 사고)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402

# --- 순수 함수 (테스트 가능) ---

UI_NOISE = {'책소개', '출판사 리뷰', '책 속으로', '접기', '펼쳐보기', '더보기'}


def isbn_matches(page_isbn, db_isbn):
    """YES24 페이지 ISBN과 DB ISBN 비교"""
    if not page_isbn or not db_isbn:
        return False
    if is_non_standard_isbn(db_isbn):
        return False
    # ISBN-13 전체 비교
    if len(page_isbn) >= 13 and len(db_isbn) >= 13:
        return page_isbn[-13:] == db_isbn[-13:]
    # ISBN-10 vs ISBN-13: db_isbn의 끝 10자리와 page_isbn의 끝 10자리 비교
    return page_isbn[-10:] == db_isbn[-10:]


def is_non_standard_isbn(isbn):
    """비표준 ISBN 여부 (K prefix, 10자 미만)"""
    if not isbn:
        return True
    return isbn.startswith('K') or len(isbn) < 10


# 제목 구분자 패턴: 부제/저자정보 이전의 핵심 제목만 추출
# `=` `/` 무조건 분리
# 공백+구분자(: = -)+공백? → 분리 (파친코 :부제, 여덟 단어 :부제)
# 한글:한글 → 분리 (고래:천명관) — 12:00 보호
TITLE_SPLIT = r'\s+[:=-][\s]?|(?<=[가-힣]):(?=[가-힣])|[=/]'


def build_search_query(title, author):
    """검색 쿼리 생성: 제목 핵심부 + 첫 번째 저자

    구분자(: = / -)로 부제 분리, 괄호 제거, 시리즈 번호 제거.
    저자명에서 역할 suffix(지음, 옮김 등) 제거.
    """
    # 핵심 제목 추출
    core = re.split(TITLE_SPLIT, title)[0]
    core = re.sub(r'\s*\(.*?\)', '', core).strip()
    # 시리즈 번호 제거 — "1984" → "" 보호 (결과가 2자 미만이면 제거 안 함)
    without_num = re.sub(r'\s+\d+\s*$', '', core).strip()
    if len(without_num) >= 2:
        core = without_num

    # 저자: 괄호 제거 + 첫 번째 저자 + 역할 suffix 제거
    clean_author = re.sub(r'\s*\(.*?\)', '', author or '').split(',')[0].strip()
    clean_author = re.sub(
        r'\s*(지음|옮김|그림|글|엮음|원작|지은이|옮긴이)\s*$', '', clean_author,
    ).strip()

    return f"{core} {clean_author}".strip()


def normalize_for_match(title):
    """제목 정규화: 공백 제거, 괄호/부제/시리즈번호/세트 제거.

    ISBN이 다른 에디션 간 매칭용. 예:
      "해리 포터와 마법사의 돌 1~2권 세트" → "해리포터와마법사의돌"
      "예루살렘의 아이히만 (알라딘 리커버 특별판)" → "예루살렘의아이히만"
    """
    t = re.sub(r'\s*\(.*?\)', '', title)
    t = re.split(TITLE_SPLIT, t)[0]
    without_num = re.sub(r'\s+\d+~?\d*권?\s*(세트)?\s*$', '', t).strip()
    t = without_num if len(without_num) >= 2 else t  # "1984" → "" 보호, "파친코 1" → "파친코" 허용
    return re.sub(r'\s+', '', t).strip().lower()


def _extract_author_from_ld(html):
    """HTML에서 JSON-LD author.name 추출. 없으면 None."""
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            ld = json.loads(m.group(1))
            author = ld.get('author')
            if isinstance(author, dict):
                return author.get('name', '')
            if isinstance(author, list) and author:
                return author[0].get('name', '')
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return None


def _extract_title_from_ld(html):
    """HTML에서 JSON-LD name(제목) 추출. 없으면 None."""
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            ld = json.loads(m.group(1))
            return ld.get('name')
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def clean_section_text(raw_text):
    """UI 노이즈 제거 후 텍스트 반환. 5자 이하이면 None."""
    lines = raw_text.split('\n')
    cleaned = '\n'.join(l for l in lines if l.strip() not in UI_NOISE)
    return cleaned.strip() if len(cleaned.strip()) > 5 else None


def extract_isbn_from_html(html):
    """HTML에서 JSON-LD의 ISBN 추출"""
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            ld = json.loads(m.group(1))
            return ld.get('gtin13') or ld.get('isbn', '')
        except (json.JSONDecodeError, KeyError):
            pass
    return None


# --- 스크래퍼 클래스 ---

class Yes24Scraper:
    DEFAULT_LIMIT = 80
    REQUEST_DELAY = 1.0
    MAX_SEARCH_RESULTS = 3
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
    }

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self._session = None
        self.stats = {
            "processed": 0, "success": 0, "search_fail": 0,
            "isbn_mismatch": 0, "isbn_skip": 0, "scrape_fail": 0, "errors": 0,
        }

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self.HEADERS)
        return self._session

    # YES24에 책소개 섹션이 없는 장르 — 스크래핑해도 결과 없음
    _SKIP_GENRES = ('만화', '코믹', '라이트노벨')

    def fetch_books_needing_scrape(self, limit):
        """rich_description이 NULL인 책 조회 (만화/코믹/라노벨 제외)"""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("books")
                .select("id, isbn, title, author, genre")
                .is_("rich_description", "null")
                .not_.is_("isbn", "null")
                .order("sales_point", desc=True)
                .range(o, o + page_size - 1)
                .execute()
            ))
            if not result.data:
                break
            for row in result.data:
                genre = row.get("genre") or ""
                if any(g in genre for g in self._SKIP_GENRES):
                    self.stats.setdefault("genre_skip", 0)
                    self.stats["genre_skip"] += 1
                    continue
                all_books.append(row)
            if len(result.data) < page_size or len(all_books) >= limit:
                break
            offset += page_size
        return all_books[:limit]

    def _search_goods_ids(self, title, author):
        """YES24 검색 → goods ID 리스트 반환"""
        query = build_search_query(title, author)
        url = f'https://www.yes24.com/Product/Search?domain=BOOK&query={urllib.parse.quote(query)}'
        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            els = soup.select('[data-goods-no]')
            return [el.get('data-goods-no') for el in els[:self.MAX_SEARCH_RESULTS]]
        except Exception:
            return []

    def _fetch_detail_page(self, goods_id):
        """상세 페이지 HTML 반환"""
        try:
            r = self.session.get(f'https://www.yes24.com/Product/Goods/{goods_id}', timeout=10)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    def _find_matching_page(self, goods_ids, expected_isbn,
                            expected_title="", expected_author=""):
        """goods ID 리스트를 순회하며 매칭되는 상세 페이지 HTML 반환.

        1차: ISBN 완전일치
        2차: ISBN 불일치 시, 제목+저자 정규화 매칭 (다른 에디션 복구)
        """
        best_title_match = None  # ISBN 불일치지만 제목+저자 매칭된 첫 페이지

        for i, goods_id in enumerate(goods_ids):
            html = self._fetch_detail_page(goods_id)
            if not html:
                continue
            page_isbn = extract_isbn_from_html(html)

            # 1차: ISBN 매칭
            if isbn_matches(page_isbn, expected_isbn):
                return html

            # 2차 후보: 제목+저자 매칭 (첫 번째만 저장)
            if best_title_match is None and expected_title:
                page_title = _extract_title_from_ld(html)
                page_author = _extract_author_from_ld(html)
                if (page_title and page_author
                        and normalize_for_match(expected_title)
                        == normalize_for_match(page_title)):
                    # 저자도 확인 (오매칭 방지)
                    db_author = re.sub(r'\s*\(.*?\)', '', expected_author or '')
                    db_author = db_author.split(',')[0].strip()
                    db_author = re.sub(
                        r'\s*(지음|옮김|그림|글|엮음|원작|지은이|옮긴이)\s*$',
                        '', db_author,
                    ).strip()
                    if db_author:
                        # 전체 이름 또는 성(마지막 단어)으로 매칭
                        # "조앤 K. 롤링" → "롤링", "J.K. 롤링" → "롤링"
                        last_name = db_author.split()[-1]
                        if db_author in page_author or (len(last_name) >= 2 and last_name in page_author):
                            best_title_match = html

            time.sleep(0.3)

        # ISBN 매칭 실패 → 제목+저자 매칭 fallback
        if best_title_match:
            self.stats.setdefault("title_matched", 0)
            self.stats["title_matched"] += 1
            return best_title_match

        return None

    def _extract_sections(self, html):
        """HTML에서 책소개/출판사리뷰/책속으로 추출"""
        soup = BeautifulSoup(html, 'html.parser')
        sections = {}
        for sid, name in [
            ('infoset_introduce', '책소개'),
            ('infoset_pubReivew', '출판사리뷰'),
            ('infoset_inBook', '책속으로'),
        ]:
            el = soup.select_one(f'#{sid}')
            if el:
                raw = el.get_text(separator='\n', strip=True)
                cleaned = clean_section_text(raw)
                if cleaned:
                    sections[name] = cleaned
        return sections

    def _save_rich_description(self, book_id, sections):
        """DB에 rich_description 저장"""
        if not sections or self.dry_run:
            return
        combined = '\n\n'.join(f'[{name}]\n{text}' for name, text in sections.items())
        with_retry(lambda: (
            self.sb.table("books")
            .update({"rich_description": combined})
            .eq("id", book_id)
            .execute()
        ))

    def run(self, limit=None):
        """메인 실행"""
        limit = limit or self.DEFAULT_LIMIT
        print(f"🔍 스크래핑 필요한 도서 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_scrape(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서가 스크래핑 완료됨.")
            return

        for i, book in enumerate(books):
            title = book['title']
            author = book.get('author', '') or ''
            isbn = book['isbn']

            try:
                if is_non_standard_isbn(isbn):
                    self.stats["isbn_skip"] += 1
                    continue

                goods_ids = self._search_goods_ids(title, author)
                if not goods_ids:
                    # fallback: 제목만으로 재시도
                    goods_ids = self._search_goods_ids(title, "")
                    if goods_ids:
                        self.stats.setdefault("search_retry_success", 0)
                        self.stats["search_retry_success"] += 1
                if not goods_ids:
                    self.stats["search_fail"] += 1
                    if self.stats["search_fail"] <= 10:
                        print(f"  ✗ 검색 실패: {title[:35]}")
                    time.sleep(self.REQUEST_DELAY)
                    continue

                time.sleep(self.REQUEST_DELAY)

                html = self._find_matching_page(
                    goods_ids, isbn,
                    expected_title=title, expected_author=author,
                )
                if not html:
                    self.stats["isbn_mismatch"] += 1
                    if self.stats["isbn_mismatch"] <= 5:
                        print(f"  ⚠ ISBN 불일치: {title[:35]}")
                    time.sleep(self.REQUEST_DELAY)
                    continue

                sections = self._extract_sections(html)
                if not sections:
                    self.stats["scrape_fail"] += 1
                    if self.stats["scrape_fail"] <= 10:
                        print(f"  ✗ 섹션 없음: {title[:35]}")
                    time.sleep(self.REQUEST_DELAY)
                    continue

                self._save_rich_description(book['id'], sections)

                total_chars = sum(len(t) for t in sections.values())
                self.stats["success"] += 1
                self.stats["processed"] += 1

                if self.stats["success"] % 25 == 0 or self.stats["success"] <= 5:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{self.stats['success']}/{len(books)}: "
                          f"{title[:25]} — {total_chars}자")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러: {title[:25]} — {e}")

            time.sleep(self.REQUEST_DELAY)

        self._print_report(len(books))

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}YES24 스크래핑 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}권")
        print(f"  성공: {s['success']}권")
        print(f"  검색 실패: {s['search_fail']}권")
        print(f"  ISBN 불일치: {s['isbn_mismatch']}권")
        print(f"  비표준 ISBN 스킵: {s['isbn_skip']}권")
        print(f"  스크래핑 실패: {s['scrape_fail']}권")
        print(f"  에러: {s['errors']}건")
        if s.get("genre_skip"):
            print(f"  만화/코믹 제외: {s['genre_skip']}권")
        if s.get("title_matched"):
            print(f"  제목+저자 매칭 복구: {s['title_matched']}권")
        if s.get("search_retry_success"):
            print(f"  검색 재시도 성공: {s['search_retry_success']}권")
        print(f"{'=' * 50}")

    def show_status(self):
        total = with_retry(lambda: self.sb.table("books").select("id", count="exact").execute())
        has_rich = with_retry(lambda: (
            self.sb.table("books").select("id", count="exact")
            .not_.is_("rich_description", "null").execute()
        ))
        pct = has_rich.count * 100 // total.count if total.count else 0
        print(f"\n{'=' * 50}")
        print("YES24 스크래핑 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  rich_description 완료: {has_rich.count}권 ({pct}%)")
        print(f"  스크래핑 필요: {total.count - has_rich.count}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="YES24 책 상세 스크래퍼")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수 (기본 80)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="진행 현황")
    args = parser.parse_args()

    scraper = Yes24Scraper(dry_run=args.dry_run)

    if args.status:
        scraper.show_status()
        return 0

    scraper.run(limit=args.limit)

    # 실패율 기반 exit code. orchestrator 의 DB 검증이 1차 guard 이지만
    # 이 스크립트 자체도 내부 성공률 급락을 감지해서 주도적으로 실패 반환.
    #
    # 기준:
    #   - 처리 대상이 없었음 → 0 (nothing to do)
    #   - 처리량 < 10권 이고 success > 0 → 0 (통계적으로 의미 없음)
    #   - success 0 건 → 1 (전멸)
    #   - success / processed < 0.5 → 1 (50% 미만 실패율)
    s = scraper.stats
    processed = s.get("processed", 0)
    success = s.get("success", 0)
    errors = s.get("errors", 0)

    if processed == 0:
        return 0
    if success == 0:
        print(f"⚠ 처리 {processed}권 전원 실패 — 재실행 권장 (idempotent)")
        return 1
    if processed >= 10 and success / processed < 0.5:
        ratio = success / processed
        print(f"⚠ 성공률 {ratio*100:.0f}% ({success}/{processed}) — 50% 미만, 재실행 권장")
        return 1
    if errors > success:
        print(f"⚠ 에러({errors}) > 성공({success}) — 재실행 권장")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
