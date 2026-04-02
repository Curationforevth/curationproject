# scripts/generate_book_v3_vectors.py
"""책별 desc 임베딩 + L1/L2 FK를 생성하여 book_v3_vectors에 저장.

선행: genre_embeddings 테이블이 채워져 있어야 함.

사용법:
  python3 scripts/generate_book_v3_vectors.py            # 전체
  python3 scripts/generate_book_v3_vectors.py 100        # 100권만
  python3 scripts/generate_book_v3_vectors.py --dry-run  # API 호출 없이 파싱만
"""
import os, sys, time, re, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_embedding
from scripts.lib.genre_parser import parse_genre, clean_html

EMBED_BATCH = 20
SLEEP_BETWEEN = 1
MAX_CONSECUTIVE_ERRORS = 3
CHECKPOINT_INTERVAL = 100


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def build_desc_source(book):
    """desc 임베딩용 소스 텍스트 생성. 스펙 섹션 3.2."""
    source = clean_html(book.get("rich_description") or "").strip()
    if not source or len(source) < 200:
        title = book.get("title", "")
        genre = book.get("genre", "")
        desc = book.get("description", "")
        source = f"{title} ({genre}) — {desc}"
    return source[:2000]


def load_genre_lookup(sb):
    """genre_embeddings → {(genre_text, level): id} dict."""
    lookup = {}
    offset = 0
    while True:
        res = sb.table("genre_embeddings").select("id, genre_text, level") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            lookup[(row["genre_text"], row["level"])] = row["id"]
        if len(res.data) < 1000:
            break
        offset += 1000
    return lookup


def get_existing_book_ids(sb):
    """이미 book_v3_vectors에 있는 book_id 집합."""
    ids = set()
    offset = 0
    while True:
        res = sb.table("book_v3_vectors").select("book_id") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        ids.update(row["book_id"] for row in res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return ids


def fetch_target_books(sb, limit):
    """rich_description이 있는 books 조회."""
    books = []
    offset = 0
    while len(books) < limit:
        res = sb.table("books") \
            .select("id, title, genre, description, rich_description") \
            .not_.is_("rich_description", "null") \
            .range(offset, offset + 499).execute()
        if not res.data:
            break
        books.extend(res.data)
        if len(res.data) < 500:
            break
        offset += 500
    return books[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("limit", nargs="?", type=int, default=99999)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sb = make_client()

    # 1. genre lookup 로드
    print("장르 FK 로드 중...", flush=True)
    genre_lookup = load_genre_lookup(sb)
    print(f"  genre_embeddings: {len(genre_lookup)}개", flush=True)
    if not genre_lookup:
        print("  ✗ genre_embeddings가 비어있습니다. generate_genre_embeddings.py를 먼저 실행하세요.", flush=True)
        sys.exit(1)

    # 2. 대상 수집 + 기존 제외
    print("대상 도서 수집 중...", flush=True)
    all_books = fetch_target_books(sb, args.limit)
    existing = get_existing_book_ids(sb)
    books = [b for b in all_books if b["id"] not in existing]
    print(f"  전체: {len(all_books)}권, 이미 처리: {len(existing)}권, 남은 대상: {len(books)}권", flush=True)

    if not books:
        print("모든 책이 이미 처리되었습니다.", flush=True)
        return

    # 3. 준비: 각 책의 desc 소스 + L1/L2 FK 매핑
    prepared = []
    no_genre_count = 0
    for book in books:
        source = build_desc_source(book)
        l1, l2 = parse_genre(book.get("genre"))
        l1_id = genre_lookup.get((l1, "l1")) if l1 else None
        l2_id = genre_lookup.get((l2, "l2")) if l2 else None
        if not l1:
            no_genre_count += 1
        prepared.append({
            "book_id": book["id"],
            "source_text": source,
            "l1_text": l1,
            "l2_text": l2,
            "l1_genre_id": l1_id,
            "l2_genre_id": l2_id,
        })

    if no_genre_count:
        print(f"  주의: 장르 없는 책 {no_genre_count}권 (desc만 저장)", flush=True)

    if args.dry_run:
        print(f"\n[dry-run] {len(prepared)}권 준비 완료. 처음 3건:")
        for p in prepared[:3]:
            print(f"  {p['book_id'][:8]}... L1={p['l1_text']} L2={p['l2_text'][:30] if p['l2_text'] else None}")
            print(f"    desc: {p['source_text'][:80]}...")
        return

    # 4. 사전 테스트 (1건)
    test = prepared[0]
    print(f"\n사전 테스트: {test['book_id'][:8]}...", flush=True)
    try:
        test_emb = call_embedding([test["source_text"]])
        assert len(test_emb) == 1 and len(test_emb[0]) == 2000
        print(f"  ✓ 임베딩 성공 (dim={len(test_emb[0])})", flush=True)
    except Exception as e:
        print(f"  ✗ 사전 테스트 실패: {e}", flush=True)
        sys.exit(1)

    # 5. 배치 처리
    start = time.time()
    done, errors, consecutive_errors = 0, 0, 0

    for i in range(0, len(prepared), EMBED_BATCH):
        batch = prepared[i:i + EMBED_BATCH]
        texts = [p["source_text"] for p in batch]

        try:
            embeddings = call_embedding(texts)
            consecutive_errors = 0
        except Exception as e:
            errors += len(batch)
            consecutive_errors += 1
            print(f"  ✗ 임베딩 실패 (연속 {consecutive_errors}회): {e}", flush=True)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n연속 에러 {MAX_CONSECUTIVE_ERRORS}회 → 자동 중단", flush=True)
                break
            time.sleep(SLEEP_BETWEEN)
            continue

        rows = []
        for p, emb in zip(batch, embeddings):
            rows.append({
                "book_id": p["book_id"],
                "desc_embedding": emb,
                "source_text": p["source_text"],
                "l1_text": p["l1_text"],
                "l2_text": p["l2_text"],
                "l1_genre_id": p["l1_genre_id"],
                "l2_genre_id": p["l2_genre_id"],
            })

        try:
            sb.table("book_v3_vectors").insert(rows).execute()
            done += len(rows)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 INSERT 실패, 1건씩 재시도: {e}", flush=True)
            for row in rows:
                try:
                    sb.table("book_v3_vectors").insert(row).execute()
                    done += 1
                except Exception as e2:
                    errors += 1
                    print(f"    ✗ {row['book_id'][:8]}...: {e2}", flush=True)

        pct = (i + len(batch)) / len(prepared) * 100
        elapsed = time.time() - start
        rate = done / elapsed * 60 if elapsed > 0 else 0
        eta = (len(prepared) - i - len(batch)) / (rate / 60) if rate > 0 else 0
        print(f"  [{pct:5.1f}%] {done}/{len(prepared)} 완료, {errors} 에러, "
              f"{elapsed/60:.1f}분경과 ~{eta:.0f}초남음", flush=True)

        if done > 0 and done % CHECKPOINT_INTERVAL == 0:
            print(f"  ── 체크포인트: {done}건 완료 ──", flush=True)

        time.sleep(SLEEP_BETWEEN)

    elapsed = time.time() - start
    print(f"\n{'='*50}", flush=True)
    print(f"book_v3_vectors 완료: {done}건 저장, {errors}건 에러, {elapsed/60:.1f}분", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
