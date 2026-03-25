"""
Tier 1 임베딩 생성기
- book_embeddings가 없는 도서를 찾아서 기본 임베딩 생성
- 입력: title + author + genre + description
- 모델: OpenAI text-embedding-3-small (1536차원)

사용법:
  python3 scripts/tier1_embedder.py              # 미생성 도서 전부
  python3 scripts/tier1_embedder.py --limit 100  # 최대 100권
  python3 scripts/tier1_embedder.py --dry-run    # 실제 저장 없이 테스트
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

try:
    from openai import OpenAI
except ImportError:
    pass  # 테스트 환경에서는 compose_embedding_text만 사용

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs): return fn()

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50  # OpenAI에 한 번에 보낼 텍스트 수


def compose_embedding_text(book):
    """책 메타데이터를 임베딩 입력 텍스트로 조합"""
    parts = []
    title = book.get("title") or ""
    author = book.get("author") or ""
    genre = book.get("genre") or ""
    description = book.get("description") or ""

    if title:
        parts.append(f"제목: {title}")
    if author:
        parts.append(f"저자: {author}")
    if genre:
        parts.append(f"장르: {genre}")
    if description:
        parts.append(f"내용: {description}")

    return "\n".join(parts)


def fetch_books_without_embeddings(sb, limit=0):
    """book_embeddings에 row가 없는 books 조회 (페이징으로 전체 조회)"""
    # 기존 임베딩 ID 수집 (페이징)
    embedded_ids = set()
    offset = 0
    page_size = 1000
    while True:
        result = with_retry(lambda o=offset: sb.table("book_embeddings").select("book_id").range(o, o + page_size - 1).execute())
        if not result.data:
            break
        for row in result.data:
            embedded_ids.add(row["book_id"])
        if len(result.data) < page_size:
            break
        offset += page_size

    # 전체 books 조회 (페이징)
    all_books = []
    offset = 0
    while True:
        result = with_retry(lambda o=offset: sb.table("books").select("id, title, author, genre, description").range(o, o + page_size - 1).execute())
        if not result.data:
            break
        all_books.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size

    books = [b for b in all_books if b["id"] not in embedded_ids]

    if limit > 0:
        books = books[:limit]

    return books


def generate_embeddings(openai_client, texts):
    """OpenAI API로 임베딩 벡터 생성"""
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def save_embeddings(sb, book_ids, embeddings, dry_run=False):
    """book_embeddings 테이블에 저장"""
    if len(embeddings) != len(book_ids):
        print(f"  ⚠ 임베딩 수({len(embeddings)})와 도서 수({len(book_ids)}) 불일치 — 배치 스킵")
        return

    if dry_run:
        return

    rows = [
        {
            "book_id": book_id,
            "embedding": embedding,
            "tier": 1,
        }
        for book_id, embedding in zip(book_ids, embeddings)
    ]

    with_retry(lambda: sb.table("book_embeddings").upsert(
        rows, on_conflict="book_id"
    ).execute())


def main():
    parser = argparse.ArgumentParser(description="Tier 1 임베딩 생성기")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 권수 (0=전부)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 테스트")
    args = parser.parse_args()

    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    print("🔍 임베딩 미생성 도서 조회 중...")
    books = fetch_books_without_embeddings(sb, limit=args.limit)
    print(f"   {len(books)}권 발견\n")

    if not books:
        print("✅ 모든 도서에 임베딩이 있습니다.")
        return

    total_embedded = 0

    for i in range(0, len(books), BATCH_SIZE):
        batch = books[i : i + BATCH_SIZE]
        texts = [compose_embedding_text(b) for b in batch]
        book_ids = [b["id"] for b in batch]

        try:
            embeddings = generate_embeddings(openai_client, texts)
            save_embeddings(sb, book_ids, embeddings, dry_run=args.dry_run)
            total_embedded += len(batch)
            prefix = "(dry-run) " if args.dry_run else ""
            print(f"  {prefix}배치 {i // BATCH_SIZE + 1}: {len(batch)}권 임베딩 완료")
        except Exception as e:
            print(f"  ✗ 배치 {i // BATCH_SIZE + 1} 실패: {e}")

        time.sleep(0.5)  # OpenAI rate limit 대비

    print(f"\n{'=' * 40}")
    prefix = "(dry-run) " if args.dry_run else ""
    print(f"{prefix}총 {total_embedded}/{len(books)}권 임베딩 생성 완료")


if __name__ == "__main__":
    main()
