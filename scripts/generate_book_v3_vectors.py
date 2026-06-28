# scripts/generate_book_v3_vectors.py
"""책별 desc 임베딩 + L1/L2 FK를 생성하여 book_v3_vectors에 저장.

선행: genre_embeddings 테이블이 채워져 있어야 함.

사용법:
  python3 scripts/generate_book_v3_vectors.py            # 전체
  python3 scripts/generate_book_v3_vectors.py 100        # 100권만
  python3 scripts/generate_book_v3_vectors.py --dry-run  # API 호출 없이 파싱만
"""
import os, sys, time, re, argparse
import json

CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), ".checkpoint_book_v3.json")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_embedding, EMBEDDING_DIMENSIONS
from scripts.lib.genre_parser import parse_genre, clean_html
from scripts.lib.retry import with_retry

EMBED_BATCH = 20
SLEEP_BETWEEN = 1
MAX_CONSECUTIVE_ERRORS = 3
CHECKPOINT_INTERVAL = 100


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


_MIN_RICH = 200


def build_desc_source(book):
    """desc 임베딩 소스 텍스트 + 품질 등급. 반환 (text|None, source_tier|None).

    단계적 폴백: rich_description≥200자(clean_html 후) → 카카오 description → title+author+genre.
    라이브 user_embed._pick_source_text 와 정책 동일(배포 경계로 코드 미공유 → 동등성
    테스트 tests/test_generate_book_v3_vectors.py 로 동기화). 얕은 텍스트도 임베딩하되
    source_tier(kakao_desc/minimal)로 표시 → 서빙이 차등 down-weight(후보 배제 아님).
    """
    rich = clean_html(book.get("rich_description") or "").strip()
    if len(rich) >= _MIN_RICH:
        return rich[:2000], "rich"
    desc = clean_html(book.get("description") or "").strip()
    if desc:
        return desc[:2000], "kakao_desc"
    parts = [book.get("title") or "", book.get("author") or "", book.get("genre") or ""]
    m = " ".join(p for p in parts if p).strip()
    return (m[:2000], "minimal") if m else (None, None)


def load_genre_lookup(sb):
    """genre_embeddings → {(genre_text, level): id} dict."""
    lookup = {}
    offset = 0
    while True:
        res = with_retry(lambda o=offset: sb.table("genre_embeddings")
                         .select("id, genre_text, level")
                         .range(o, o + 999).execute())
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
        res = with_retry(lambda o=offset: sb.table("book_v3_vectors")
                         .select("book_id")
                         .range(o, o + 999).execute())
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
        res = with_retry(lambda o=offset: sb.table("books")
                         .select("id, title, genre, description, rich_description")
                         .not_.is_("rich_description", "null")
                         .range(o, o + 999).execute())
        if not res.data:
            break
        books.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return books[:limit]


def load_checkpoint():
    """체크포인트 파일에서 완료된 book_id 목록 로드."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
            print(f"  체크포인트 로드: {len(data.get('done_ids', []))}건", flush=True)
            return set(data.get("done_ids", []))
    return set()


def save_checkpoint(done_ids):
    """처리 완료된 book_id를 체크포인트 파일에 저장."""
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"done_ids": list(done_ids), "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)


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
    checkpoint_ids = load_checkpoint()
    existing = existing | checkpoint_ids
    books = [b for b in all_books if b["id"] not in existing]
    print(f"  전체: {len(all_books)}권, 이미 처리: {len(existing)}권, 남은 대상: {len(books)}권", flush=True)

    if not books:
        print("모든 책이 이미 처리되었습니다.", flush=True)
        return

    # 3. 준비: 각 책의 desc 소스 + L1/L2 FK 매핑
    prepared = []
    no_genre_count = 0
    skipped_shallow = 0
    for book in books:
        source, tier = build_desc_source(book)
        if source is None:
            # 텍스트 전무(rich/description/title 모두 없음)만 SKIP. 빈약해도 가진
            # 정보가 있으면 tier(kakao_desc/minimal)로 임베딩 → 후보풀 편입.
            skipped_shallow += 1
            continue
        l1, l2 = parse_genre(book.get("genre"))
        l1_id = genre_lookup.get((l1, "l1")) if l1 else None
        l2_id = genre_lookup.get((l2, "l2")) if l2 else None
        if not l1:
            no_genre_count += 1
        prepared.append({
            "book_id": book["id"],
            "source_text": source,
            "source_tier": tier,
            "l1_text": l1,
            "l2_text": l2,
            "l1_genre_id": l1_id,
            "l2_genre_id": l2_id,
        })

    if no_genre_count:
        print(f"  주의: 장르 없는 책 {no_genre_count}권 (desc만 저장)", flush=True)
    if skipped_shallow:
        print(f"  텍스트 전무 SKIP: {skipped_shallow}권 "
              f"(rich/description/title 모두 없음)", flush=True)

    # prepared 가 비면(전부 텍스트 전무) 아래 prepared[0] 가 IndexError → 크래시. 빈 가드.
    if not prepared:
        print("  처리할 책 없음 (전부 텍스트 전무 또는 미처리 0)", flush=True)
        return

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
        assert len(test_emb) == 1 and len(test_emb[0]) == EMBEDDING_DIMENSIONS
        print(f"  ✓ 임베딩 성공 (dim={len(test_emb[0])})", flush=True)
    except Exception as e:
        print(f"  ✗ 사전 테스트 실패: {e}", flush=True)
        sys.exit(1)

    # 5. 배치 처리
    start = time.time()
    done, errors, consecutive_errors = 0, 0, 0
    successfully_saved = set()  # 실제 DB 저장 성공한 book_id만 추적

    for i in range(0, len(prepared), EMBED_BATCH):
        batch = prepared[i:i + EMBED_BATCH]
        texts = [p["source_text"] for p in batch]

        try:
            embeddings = call_embedding(texts)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 임베딩 실패, 1건씩 재시도: {e}", flush=True)
            embeddings = []
            for t in texts:
                try:
                    emb = call_embedding([t])
                    embeddings.append(emb[0])
                except Exception as e2:
                    embeddings.append(None)
                    errors += 1
                    print(f"    ✗ 개별 임베딩 실패: {e2}", flush=True)
                time.sleep(SLEEP_BETWEEN)
            consecutive_errors = 0

        rows = []
        for p, emb in zip(batch, embeddings):
            if emb is None:
                continue
            rows.append({
                "book_id": p["book_id"],
                "desc_embedding": emb,
                "source_text": p["source_text"],
                "source_tier": p["source_tier"],
                "provisional": p["source_tier"] != "rich",
                "l1_text": p["l1_text"],
                "l2_text": p["l2_text"],
                "l1_genre_id": p["l1_genre_id"],
                "l2_genre_id": p["l2_genre_id"],
            })

        if not rows:
            time.sleep(SLEEP_BETWEEN)
            continue

        try:
            with_retry(lambda: sb.table("book_v3_vectors").upsert(rows, on_conflict="book_id").execute())
            done += len(rows)
            successfully_saved.update(row["book_id"] for row in rows)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 UPSERT 실패, 1건씩 재시도: {e}", flush=True)
            for row in rows:
                try:
                    with_retry(lambda r=row: sb.table("book_v3_vectors").upsert(r, on_conflict="book_id").execute())
                    done += 1
                    successfully_saved.add(row["book_id"])
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
            all_done = existing | successfully_saved
            save_checkpoint(all_done)
            print(f"  ── 체크포인트: {done}건 완료, 상태 저장 ──", flush=True)

        time.sleep(SLEEP_BETWEEN)

    # 루프 종료 후 최종 체크포인트 저장 (성공한 것만)
    if successfully_saved:
        all_done = existing | successfully_saved
        save_checkpoint(all_done)

    elapsed = time.time() - start
    print(f"\n{'='*50}", flush=True)
    print(f"book_v3_vectors 완료: {done}건 저장, {errors}건 에러, {elapsed/60:.1f}분", flush=True)
    print(f"{'='*50}", flush=True)
    if errors == 0 and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("  체크포인트 파일 삭제 (정상 완료)", flush=True)

    # C5 (M4): errors > 0 이면 exit 1 — cron/orchestrator 가 감지 가능.
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
