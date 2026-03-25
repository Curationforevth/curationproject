"""
Tier 2 임베딩 생성기 — 점진적 임베딩 파이프라인

rich_description이 있는 도서의 임베딩을 업그레이드.
데이터 소스가 추가될 때마다 임베딩이 풍성해지는 구조.

사용법:
  python3 scripts/tier2_embedder.py                      # 미처리분
  python3 scripts/tier2_embedder.py --limit 300           # 최대 300권
  python3 scripts/tier2_embedder.py --force --limit 500   # 강제 재생성
  python3 scripts/tier2_embedder.py --dry-run             # DB 저장 없이 테스트
  python3 scripts/tier2_embedder.py --status              # 현황 조회
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

try:
    from openai import OpenAI
except ImportError:
    pass  # 테스트 환경에서는 순수 함수만 사용

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50
MAX_CHARS = 15000  # ~7500 토큰 (한국어 ~2자 = ~1토큰)
EXCERPT_LIMIT = 300


def parse_section(rich_description, section_name):
    """rich_description에서 특정 섹션 텍스트 추출.

    포맷: [책소개]\\n텍스트\\n\\n[출판사리뷰]\\n텍스트\\n\\n[책속으로]\\n텍스트
    """
    if not rich_description:
        return ""

    marker = f"[{section_name}]"
    if marker not in rich_description:
        return ""

    after = rich_description.split(marker, 1)[1]

    next_markers = ['[책소개]', '[출판사리뷰]', '[책속으로]']
    end = len(after)
    for m in next_markers:
        if m != marker and m in after:
            idx = after.index(m)
            if idx < end:
                end = idx

    return after[:end].strip()


def clean_intro(text):
    """책소개 텍스트에서 노이즈 라인 제거."""
    if not text:
        return ""

    lines = text.split('\n')
    cleaned = []
    skip_md = False

    for line in lines:
        stripped = line.strip()

        # ★ 로 시작하는 마케팅 라인
        if stripped.startswith('★'):
            continue

        # MD 한마디 블록 (MD 한마디 ~ 다음 빈 줄까지)
        if stripped.startswith('MD 한마디') or stripped.startswith('MD한마디'):
            skip_md = True
            continue
        if skip_md:
            if stripped == '':
                skip_md = False
            continue

        # 미리보기 라인
        if '미리보기' in stripped:
            continue

        cleaned.append(line)

    return '\n'.join(cleaned).strip()


def compose_embedding(book):
    """가용한 데이터를 모두 활용하여 임베딩 텍스트 조합.

    Returns:
        (text, data_sources): 텍스트와 사용된 소스 목록.
        rich_description이 없거나 책소개 파싱 실패 시 ("", []) 반환.
    """
    rd = book.get('rich_description')
    if not rd:
        return "", []

    # 책소개 필수 (Tier 2 최소 조건)
    intro = clean_intro(parse_section(rd, '책소개'))
    if not intro:
        return "", []

    parts = []
    data_sources = ['aladin']

    # 기본 메타데이터 (항상)
    if book.get('title'):
        parts.append(f"제목: {book['title']}")
    if book.get('author'):
        parts.append(f"저자: {book['author']}")
    if book.get('genre'):
        parts.append(f"장르: {book['genre']}")
    if book.get('description'):
        parts.append(f"내용: {book['description']}")

    # YES24 책소개
    parts.append(f"책소개: {intro}")
    data_sources.append('yes24_intro')

    # YES24 책속으로 발췌
    excerpt = parse_section(rd, '책속으로')
    if excerpt:
        parts.append(f"발췌: {excerpt[:EXCERPT_LIMIT]}")
        data_sources.append('yes24_excerpt')

    # (미래) 도서관 키워드
    # if book.get('library_keywords'):
    #     parts.append(f"키워드: {', '.join(book['library_keywords'])}")
    #     data_sources.append('library_keywords')

    text = '\n'.join(parts)

    # 토큰 안전장치
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    return text, data_sources


class Tier2Embedder:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self._openai_client = None  # lazy init — --status에서는 불필요
        self.stats = {"embedded": 0, "skipped": 0, "errors": 0}

    @property
    def openai_client(self):
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._openai_client

    def fetch_books_needing_tier2(self, limit=0, force=False):
        """rich_description이 있고, Tier 2 임베딩이 아직 없는 책 조회."""
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books") \
                .select("id, title, author, genre, description, rich_description") \
                .not_.is_("rich_description", "null") \
                .range(o, o + page_size - 1) \
                .execute())
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

        if force:
            books = all_books
        else:
            # 이미 Tier 2 임베딩이 있는 book_id 수집
            tier2_ids = set()
            offset = 0
            while True:
                result = with_retry(lambda o=offset: self.sb.table("book_embeddings") \
                    .select("book_id") \
                    .eq("tier", 2) \
                    .range(o, o + page_size - 1) \
                    .execute())
                if not result.data:
                    break
                for row in result.data:
                    tier2_ids.add(row["book_id"])
                if len(result.data) < page_size:
                    break
                offset += page_size

            books = [b for b in all_books if b["id"] not in tier2_ids]

        if limit > 0:
            books = books[:limit]

        return books

    def run(self, limit=0, force=False):
        """메인 실행."""
        print(f"🔍 Tier 2 대상 도서 조회 중... {'(force)' if force else ''}")
        books = self.fetch_books_needing_tier2(limit=limit, force=force)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ Tier 2 임베딩이 필요한 도서가 없습니다.")
            return

        # compose + filter
        valid_books = []
        for book in books:
            text, sources = compose_embedding(book)
            if text:
                valid_books.append((book, text, sources))
            else:
                self.stats["skipped"] += 1

        if not valid_books:
            print(f"⚠ 유효한 텍스트를 생성할 수 없는 도서 {self.stats['skipped']}권 스킵.")
            return

        print(f"  {len(valid_books)}권 처리 예정 ({self.stats['skipped']}권 스킵)\n")

        # 배치 임베딩
        for i in range(0, len(valid_books), BATCH_SIZE):
            batch = valid_books[i : i + BATCH_SIZE]
            texts = [t for _, t, _ in batch]
            book_ids = [b["id"] for b, _, _ in batch]
            sources_list = [s for _, _, s in batch]

            try:
                response = self.openai_client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                )
                embeddings = [item.embedding for item in response.data]

                if len(embeddings) != len(book_ids):
                    print(f"  ⚠ 임베딩 수({len(embeddings)})와 도서 수({len(book_ids)}) 불일치 — 배치 스킵")
                    self.stats["errors"] += 1
                    continue

                if not self.dry_run:
                    rows = [
                        {
                            "book_id": bid,
                            "embedding": emb,
                            "tier": 2,
                            "source_text": txt,
                            "data_sources": src,
                        }
                        for bid, emb, txt, src in zip(book_ids, embeddings, texts, sources_list)
                    ]
                    with_retry(lambda: self.sb.table("book_embeddings").upsert(
                        rows, on_conflict="book_id"
                    ).execute())

                self.stats["embedded"] += len(batch)
                prefix = "(dry-run) " if self.dry_run else ""
                print(f"  {prefix}배치 {i // BATCH_SIZE + 1}: {len(batch)}권 Tier 2 임베딩 완료")

            except Exception as e:
                self.stats["errors"] += 1
                print(f"  ✗ 배치 {i // BATCH_SIZE + 1} 실패: {e}")

            time.sleep(0.5)

        self.print_report()

    def print_report(self):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}Tier 2 임베딩 결과")
        print(f"{'=' * 50}")
        print(f"  임베딩 완료: {s['embedded']}권")
        print(f"  스킵 (책소개 없음): {s['skipped']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = with_retry(lambda: self.sb.table("books").select("id", count="exact").execute())
        has_rich = with_retry(lambda: self.sb.table("books").select("id", count="exact") \
            .not_.is_("rich_description", "null").execute())

        tier1 = with_retry(lambda: self.sb.table("book_embeddings").select("id", count="exact") \
            .eq("tier", 1).execute())
        tier2 = with_retry(lambda: self.sb.table("book_embeddings").select("id", count="exact") \
            .eq("tier", 2).execute())

        print(f"\n{'=' * 50}")
        print("Tier 2 임베딩 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  rich_description 수집 완료: {has_rich.count}권")
        print(f"  Tier 1 임베딩: {tier1.count}권")
        print(f"  Tier 2 임베딩: {tier2.count}권")
        print(f"  Tier 2 대기: {has_rich.count - tier2.count}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="Tier 2 임베딩 생성기")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 권수 (0=전부)")
    parser.add_argument("--force", action="store_true", help="강제 재생성 (--limit 필수)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="현황 조회")
    args = parser.parse_args()

    if args.force and args.limit == 0:
        print("❌ --force 사용 시 --limit을 반드시 지정해주세요.")
        sys.exit(1)

    embedder = Tier2Embedder(dry_run=args.dry_run)

    if args.status:
        embedder.show_status()
        return

    embedder.run(limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()
