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

# `lib.retry.with_retry` 는 hard dependency — silent no-op fallback 은 금지.
# (과거: 패스 문제로 retry 가 통째로 no-op 되어 수백 권 drop 하고도
#  exit 0 으로 끝나는 사고가 있었음. 반드시 실제 retry 가 돌아야 한다.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402

EMBEDDING_MODEL = "text-embedding-3-small"

# OpenAI 한 번에 보낼 텍스트 수. DB 부하로 timeout (57014) 시 자동으로 축소.
BATCH_SIZE = 50
BATCH_SIZE_FALLBACKS = [50, 20, 5]  # 57014 나면 다음 단계로 축소 재시도


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


def _is_statement_timeout(exc):
    """Supabase upsert 가 Postgres statement_timeout (57014) 로 실패했는지."""
    return str(getattr(exc, "code", "") or "") == "57014"


def save_embeddings_chunk(sb, book_ids, embeddings, dry_run=False):
    """단일 chunk 를 book_embeddings 에 upsert. 예외는 그대로 전파한다.

    lib.retry.with_retry 가 이미 57014 등 일시 에러에 대해 backoff 재시도를
    수행한다. 이 함수는 chunk 단위 실패를 부르는 caller 에게 던져서
    caller 가 chunk 크기를 줄여 재시도할 수 있게 한다.
    """
    if len(embeddings) != len(book_ids):
        raise ValueError(
            f"임베딩 수({len(embeddings)})와 도서 수({len(book_ids)}) 불일치"
        )

    if dry_run:
        return

    rows = [
        {"book_id": book_id, "embedding": embedding, "tier": 1}
        for book_id, embedding in zip(book_ids, embeddings)
    ]

    with_retry(
        lambda: sb.table("book_embeddings")
        .upsert(rows, on_conflict="book_id")
        .execute()
    )


def save_embeddings_with_fallback(sb, book_ids, embeddings, dry_run=False):
    """Chunk 크기를 줄여가며 저장 시도.

    첫 upsert 가 statement_timeout 으로 실패하면 `BATCH_SIZE_FALLBACKS`
    순서로 chunk 를 쪼개 다시 시도한다. 개별 chunk 에서 57014 가 아닌
    영구 에러가 나면 그 chunk 만 실패로 기록하고 남은 chunk 는 계속 시도.

    Returns: (saved_count, failed_count)
    """
    total = len(book_ids)
    # 첫 시도: 전체를 한 번에 (가장 빠른 경로)
    try:
        save_embeddings_chunk(sb, book_ids, embeddings, dry_run=dry_run)
        return total, 0
    except Exception as e:
        if not _is_statement_timeout(e):
            # 진짜 영구 에러 — 재시도 의미 없음
            print(f"  ✗ 영구 에러로 chunk 실패 ({total}권): {e}")
            return 0, total
        print(f"  ⚠ statement_timeout — chunk 크기 축소 재시도 ({total}권)")

    # 첫 시도가 실패했으므로 바로 다음 fallback 단계부터 시작.
    # queue 에 들어가는 chunk 는 항상 "아직 시도 안 한 크기" 여야 함.
    def _next_smaller_size(current: int):
        for fb in BATCH_SIZE_FALLBACKS:
            if fb < current:
                return fb
        return None  # 이미 최소 단위

    initial_size = _next_smaller_size(total)
    saved = 0
    failed = 0
    queue = []
    if initial_size is None:
        # 이미 BATCH_SIZE_FALLBACKS 최소값 이하 — 1권씩 쪼개기
        if total > 1:
            for j in range(total):
                queue.append((book_ids[j:j+1], embeddings[j:j+1]))
        else:
            print(f"  ✗ 1권 chunk 에서도 57014 — 포기 ({book_ids[0]})")
            return 0, 1
    else:
        for j in range(0, total, initial_size):
            queue.append((book_ids[j:j+initial_size], embeddings[j:j+initial_size]))

    while queue:
        cur_ids, cur_embs = queue.pop(0)
        size = len(cur_ids)
        try:
            save_embeddings_chunk(sb, cur_ids, cur_embs, dry_run=dry_run)
            saved += size
            continue
        except Exception as e:
            if not _is_statement_timeout(e):
                print(f"  ✗ chunk 영구 에러 ({size}권): {e}")
                failed += size
                continue
            next_size = _next_smaller_size(size)
            if next_size is None:
                # 최소 fallback 에서도 timeout → 1권씩 마지막 시도
                if size > 1:
                    for j in range(size):
                        queue.append((cur_ids[j:j+1], cur_embs[j:j+1]))
                    continue
                print(f"  ✗ 1권 chunk 에서도 57014 — 포기 ({cur_ids[0]})")
                failed += 1
                continue
            for j in range(0, size, next_size):
                queue.append((cur_ids[j:j+next_size], cur_embs[j:j+next_size]))

    return saved, failed


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
        return 0

    total_saved = 0
    total_failed = 0
    openai_failed_batches = 0

    for i in range(0, len(books), BATCH_SIZE):
        batch = books[i : i + BATCH_SIZE]
        texts = [compose_embedding_text(b) for b in batch]
        book_ids = [b["id"] for b in batch]
        batch_num = i // BATCH_SIZE + 1

        # OpenAI 임베딩 생성 — 여기 실패는 chunk fallback 대상 아님
        try:
            embeddings = generate_embeddings(openai_client, texts)
        except Exception as e:
            print(f"  ✗ 배치 {batch_num}: OpenAI 실패 — {e}")
            total_failed += len(batch)
            openai_failed_batches += 1
            time.sleep(0.5)
            continue

        # DB 저장 — fallback 포함
        saved, failed = save_embeddings_with_fallback(
            sb, book_ids, embeddings, dry_run=args.dry_run
        )
        total_saved += saved
        total_failed += failed
        prefix = "(dry-run) " if args.dry_run else ""
        status = "완료" if failed == 0 else f"부분 실패 ({failed}권 drop)"
        print(f"  {prefix}배치 {batch_num}: {saved}/{len(batch)}권 {status}")

        time.sleep(0.5)  # OpenAI rate limit 대비

    print(f"\n{'=' * 40}")
    prefix = "(dry-run) " if args.dry_run else ""
    print(f"{prefix}총 {total_saved}/{len(books)}권 임베딩 생성 완료")
    if total_failed > 0:
        print(f"⚠ 실패 {total_failed}권 (OpenAI 실패 배치: {openai_failed_batches})")
        print(f"  → 원인 확인 후 재실행하세요 (idempotent).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
