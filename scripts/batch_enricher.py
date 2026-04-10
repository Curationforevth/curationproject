"""
배치 메타데이터 보강기 — 색상 추출 + 폰트 배정

books 테이블에서 dominant_colors 또는 spine_font가 NULL인 책을 찾아
cover 이미지에서 색상 추출, 장르 기반 폰트 배정을 수행.

매일 배치 수집 후 실행. 한 번에 다 못하면 다음 날 이어서.

사용법:
  python3 scripts/batch_enricher.py                  # 기본 (200권)
  python3 scripts/batch_enricher.py --limit 500      # 최대 500권
  python3 scripts/batch_enricher.py --dry-run        # DB 저장 없이 테스트
  python3 scripts/batch_enricher.py --status         # 진행 현황
"""

import argparse
import io
import os
import sys
import time
import urllib.request
import urllib.error

from dotenv import load_dotenv
from supabase import create_client

# `lib.retry.with_retry` 는 hard dependency — silent no-op fallback 은 금지.
# (과거: 패스 문제로 retry 가 통째로 no-op 되어 수백 권 drop 하고도
#  exit 0 으로 끝나는 사고가 있었음. 반드시 실제 retry 가 돌아야 한다.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402

load_dotenv()

# colorthief는 선택 의존성
try:
    from colorthief import ColorThief
    HAS_COLORTHIEF = True
except ImportError:
    HAS_COLORTHIEF = False
    print("⚠ colorthief 미설치 — 색상 추출 스킵 (pip install colorthief)")

# ── 폰트 배정 (Dart FontAssigner 로직 복제) ──────────────────

FONT_POOL = {
    'Nanum Myeongjo': ['문학', '소설', '고전', '순수문학'],
    'Noto Serif KR': ['역사', '논픽션', '사회', '전쟁'],
    'Black Han Sans': ['스릴러', '추리', '범죄', '사회고발'],
    'Gowun Batang': ['시', '에세이', '산문', '수필'],
    'Do Hyeon': ['SF', 'IT', '과학', '현대'],
    'Jua': ['힐링', '일상', '가족', '요리', '여행'],
    'Gaegu': ['판타지', '동화', '청소년', '만화'],
}

DEFAULT_FONT = 'Pretendard'


def assign_font(genre, description):
    """장르/설명에서 키워드 매칭으로 폰트 결정"""
    text = f"{genre or ''} {description or ''}".lower()
    for font_name, keywords in FONT_POOL.items():
        for kw in keywords:
            if kw in text:
                return font_name
    return DEFAULT_FONT


# ── 색상 추출 ──────────────────────────────────────────

def extract_colors(cover_url, max_colors=3, timeout=10):
    """커버 이미지 URL에서 dominant color 추출 → hex 리스트"""
    if not cover_url or not HAS_COLORTHIEF:
        return None

    try:
        req = urllib.request.Request(cover_url, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            img_data = resp.read()

        ct = ColorThief(io.BytesIO(img_data))
        palette = ct.get_palette(color_count=max_colors, quality=5)

        return [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in palette[:max_colors]]
    except Exception:
        return None


# ── 메인 ──────────────────────────────────────────

class BatchEnricher:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self.stats = {
            "processed": 0,
            "colors_extracted": 0,
            "colors_failed": 0,
            "fonts_assigned": 0,
            "errors": 0,
        }

    def fetch_books_needing_enrichment(self, limit=200):
        """dominant_colors 또는 spine_font가 NULL인 책 조회 (페이징)"""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books") \
                .select("id, cover_url, genre, description, dominant_colors, spine_font") \
                .or_("dominant_colors.is.null,spine_font.is.null") \
                .range(o, o + page_size - 1) \
                .execute())
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size or (limit > 0 and len(all_books) >= limit):
                break
            offset += page_size

        if limit > 0:
            all_books = all_books[:limit]
        return all_books

    def enrich_book(self, book):
        """단일 책 보강 — 색상 + 폰트"""
        updates = {}

        # 색상 추출 (NULL일 때만)
        if book.get("dominant_colors") is None and book.get("cover_url"):
            colors = extract_colors(book["cover_url"])
            if colors:
                updates["dominant_colors"] = colors
                self.stats["colors_extracted"] += 1
            else:
                self.stats["colors_failed"] += 1

        # 폰트 배정 (NULL일 때만)
        if book.get("spine_font") is None:
            font = assign_font(book.get("genre"), book.get("description"))
            updates["spine_font"] = font
            self.stats["fonts_assigned"] += 1

        return updates

    def run(self, limit=200):
        """메인 실행"""
        print(f"🔍 보강 필요한 도서 조회 중... (최대 {limit}권)")
        books = self.fetch_books_needing_enrichment(limit)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ 모든 도서가 보강 완료됨.")
            return 0

        for i, book in enumerate(books):
            try:
                updates = self.enrich_book(book)

                if updates and not self.dry_run:
                    with_retry(lambda u=updates, bid=book["id"]: self.sb.table("books").update(u).eq("id", bid).execute())

                self.stats["processed"] += 1

                if (i + 1) % 50 == 0:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{i + 1}/{len(books)}권 처리 완료")

            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ {book.get('id', '?')}: {e}")

            # 이미지 다운로드 부하 방지
            time.sleep(0.1)

        self.print_report(len(books))
        return 1 if self.stats["errors"] > 0 else 0

    def print_report(self, total):
        """결과 리포트"""
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}보강 결과 리포트")
        print(f"{'=' * 50}")
        print(f"  처리: {s['processed']}/{total}권")
        print(f"  색상 추출: {s['colors_extracted']}권")
        print(f"  색상 추출 실패: {s['colors_failed']}권")
        print(f"  폰트 배정: {s['fonts_assigned']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        """현재 보강 현황"""
        total = with_retry(lambda: self.sb.table("books").select("id", count="exact").execute())
        has_colors = with_retry(lambda: self.sb.table("books").select("id", count="exact").not_.is_("dominant_colors", "null").execute())
        has_font = with_retry(lambda: self.sb.table("books").select("id", count="exact").not_.is_("spine_font", "null").execute())

        print(f"\n{'=' * 50}")
        print("메타데이터 보강 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  색상 추출 완료: {has_colors.count}권 ({has_colors.count / total.count * 100:.0f}%)" if total.count else "")
        print(f"  폰트 배정 완료: {has_font.count}권 ({has_font.count / total.count * 100:.0f}%)" if total.count else "")
        print(f"  보강 필요: {total.count - min(has_colors.count, has_font.count)}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="배치 메타데이터 보강기")
    parser.add_argument("--limit", type=int, default=200, help="최대 처리 권수 (기본 200)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="보강 현황 조회")
    args = parser.parse_args()

    enricher = BatchEnricher(dry_run=args.dry_run)

    if args.status:
        enricher.show_status()
        return 0

    return enricher.run(limit=args.limit) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
