"""Reason Extractor — 책의 "좋아할 이유" 추출 파이프라인

도서 메타데이터 기반으로 LLM을 활용해 "이 책을 좋아할 이유"를 추출하고,
임베딩과 함께 DB에 저장한다.

사용법:
  python3 scripts/reason_extractor.py                  # 미처리분
  python3 scripts/reason_extractor.py --limit 100      # 최대 100권
  python3 scripts/reason_extractor.py --dry-run        # DB 저장 없이 테스트
  python3 scripts/reason_extractor.py --status          # 커버리지 현황
"""

import argparse
import os
import re
import time

from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

try:
    from lib.openai_helpers import call_chat, call_embedding
except ImportError:
    pass  # 테스트 환경에서는 순수 함수만 사용

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()

MIN_REASON_LENGTH = 4
BATCH_SIZE = 20

# 범용 표현 패턴 — 구체적이지 않은 이유 필터링용
GENERIC_PATTERNS = [
    "재밌다", "감동적이다", "좋은 책", "읽어볼 만하다",
    "추천한다", "괜찮다", "잘 읽힌다", "흥미롭다", "인상적이다",
]


# ──────────────────────────────────────────────
# 순수 함수 (API 호출 없이 테스트 가능)
# ──────────────────────────────────────────────

def build_extraction_prompt(title, genre, description, library_keywords):
    """도서 메타데이터 기반 이유 추출 LLM 프롬프트 구성."""
    parts = [f"도서 제목: {title}"]

    if genre:
        parts.append(f"장르: {genre}")
    if description:
        # HTML 태그 제거
        clean_desc = re.sub(r"<[^>]+>", "", description)
        parts.append(f"설명: {clean_desc}")
    if library_keywords:
        parts.append(f"키워드: {', '.join(library_keywords)}")

    book_info = "\n".join(parts)

    prompt = f"""다음 도서를 좋아할 이유를 추출해주세요.

{book_info}

규칙:
- 특정 판본/에디션이 아닌 작품 자체의 매력
- 설명에 없더라도 이 작품에 대해 알고 있는 내용을 활용
- 10~30단어의 구체적인 한 문장
- '이 책의~' 서두 없이 핵심만
- 범용 표현 제외 (예: 재밌다, 감동적이다, 좋은 책)
- 유의미한 이유만 (3~8개)

JSON 형식으로 응답:
{{"reasons": ["이유1", "이유2", ...]}}"""

    return prompt


def build_feedback_prompt(feedback_text):
    """사용자 피드백 기반 이유 추출 LLM 프롬프트 구성."""
    prompt = f"""다음 사용자 피드백에서 이 책을 좋아하는 구체적인 이유를 추출해주세요.

피드백: {feedback_text}

규칙:
- 피드백에서 직접 언급하거나 암시하는 것만
- 2~6단어의 짧은 구
- 없는 말 만들지 마세요
- 피드백이 너무 모호하면 빈 리스트 반환

JSON 형식으로 응답:
{{"reasons": ["이유1", "이유2", ...]}}"""

    return prompt


def parse_reasons(raw_response):
    """LLM JSON 응답에서 이유 리스트 추출. 빈 문자열/공백 필터링."""
    if not isinstance(raw_response, dict):
        return []
    reasons = raw_response.get("reasons", [])
    if not isinstance(reasons, list):
        return []
    return [r.strip() for r in reasons if isinstance(r, str) and r.strip()]


def filter_generic_reasons(reasons):
    """범용/모호한 표현 필터링. 짧은 이유도 제거."""
    filtered = []
    for reason in reasons:
        # 길이 필터
        if len(reason) < MIN_REASON_LENGTH:
            continue
        # 범용 표현 필터
        is_generic = False
        for pattern in GENERIC_PATTERNS:
            if reason.strip() == pattern or reason.strip().rstrip('.') == pattern:
                is_generic = True
                break
        if not is_generic:
            filtered.append(reason)
    return filtered


# ──────────────────────────────────────────────
# ReasonExtractor 클래스 (파이프라인)
# ──────────────────────────────────────────────

class ReasonExtractor:
    def __init__(self, sb, dry_run=False):
        self.sb = sb
        self.dry_run = dry_run
        self.stats = {
            "processed": 0,
            "reasons_created": 0,
            "skipped": 0,
            "errors": 0,
        }

    def run(self, limit=None):
        """메인 배치 루프: 이유 미추출 도서 조회 → 추출 → 임베딩 → 저장."""
        print("🔍 이유 미추출 도서 조회 중...")

        # 이미 처리된 book_id 수집
        processed_ids = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: self.sb.table("book_love_reasons")
                .select("book_id")
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            for row in result.data:
                processed_ids.add(row["book_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        # 도서 조회 (sales_point DESC — 인기순)
        all_books = []
        offset = 0
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books")
                .select("id, title, genre, description, rich_description, library_keywords, sales_point")
                .order("sales_point", desc=True)
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

        # 이미 처리된 도서 제외
        books = [b for b in all_books if b["id"] not in processed_ids]

        if limit:
            books = books[:limit]

        print(f"   {len(books)}권 대상 ({len(processed_ids)}권 이미 처리됨)\n")

        if not books:
            print("✅ 이유 추출이 필요한 도서가 없습니다.")
            return

        for i, book in enumerate(books):
            try:
                self.extract_and_save(book)
                self.stats["processed"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                print(f"  ✗ [{book.get('title', '?')}] 실패: {e}")

            # 레이트 리밋
            if i < len(books) - 1:
                time.sleep(0.3)

            # 진행 상황
            if (i + 1) % BATCH_SIZE == 0:
                print(f"  ... {i + 1}/{len(books)}권 처리 완료")

        self.print_report()

    def extract_and_save(self, book):
        """단일 도서: LLM 추출 → 필터 → 임베딩 → DB 저장."""
        title = book.get("title", "")
        genre = book.get("genre", "")
        description = book.get("description", "")
        rich_desc = book.get("rich_description", "")

        # rich_description에서 HTML 태그 제거 후 description 보강
        if rich_desc:
            clean_rich = re.sub(r"<[^>]+>", "", rich_desc)
            if len(clean_rich) > len(description or ""):
                description = clean_rich

        library_keywords = book.get("library_keywords")

        # LLM 이유 추출
        prompt = build_extraction_prompt(title, genre, description, library_keywords)
        raw = call_chat(prompt)
        reasons = parse_reasons(raw)
        reasons = filter_generic_reasons(reasons)

        if not reasons:
            self.stats["skipped"] += 1
            print(f"  ⊘ [{title}] 유의미한 이유 없음 — 스킵")
            return

        # 이유 임베딩
        embeddings = call_embedding(reasons)

        if not self.dry_run:
            rows = []
            for reason, embedding in zip(reasons, embeddings):
                rows.append({
                    "book_id": book["id"],
                    "reason": reason,
                    "embedding": embedding,
                    "source": "extraction",
                })
            with_retry(lambda: self.sb.table("book_love_reasons")
                .insert(rows)
                .execute())

        self.stats["reasons_created"] += len(reasons)
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"  {prefix}✓ [{title}] {len(reasons)}개 이유 추출")

    def print_report(self):
        """배치 결과 출력."""
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}Reason Extractor 결과")
        print(f"{'=' * 50}")
        print(f"  처리 완료: {s['processed']}권")
        print(f"  이유 생성: {s['reasons_created']}건")
        print(f"  스킵 (이유 없음): {s['skipped']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    @staticmethod
    def get_status(sb):
        """커버리지 현황 출력."""
        total_books = with_retry(lambda: sb.table("books")
            .select("id", count="exact")
            .execute())

        # book_love_reasons에서 고유 book_id 수 조회
        reason_book_ids = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: sb.table("book_love_reasons")
                .select("book_id")
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            for row in result.data:
                reason_book_ids.add(row["book_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        total_reasons = with_retry(lambda: sb.table("book_love_reasons")
            .select("id", count="exact")
            .execute())

        covered = len(reason_book_ids)
        total = total_books.count or 0
        pct = (covered / total * 100) if total > 0 else 0

        print(f"\n{'=' * 50}")
        print("Reason Extractor 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total}권")
        print(f"  이유 추출 완료: {covered}권 ({pct:.1f}%)")
        print(f"  총 이유 수: {total_reasons.count}건")
        print(f"  미추출: {total - covered}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="Reason Extractor — 좋아할 이유 추출")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="커버리지 현황 조회")
    args = parser.parse_args()

    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )

    if args.status:
        ReasonExtractor.get_status(sb)
        return

    extractor = ReasonExtractor(sb, dry_run=args.dry_run)
    extractor.run(limit=args.limit)


if __name__ == "__main__":
    main()
