# scripts/generate_genre_embeddings.py
"""고유 장르 텍스트의 임베딩을 생성하여 genre_embeddings 테이블에 저장.

사용법:
  python3 scripts/generate_genre_embeddings.py          # 전체
  python3 scripts/generate_genre_embeddings.py --dry-run # 파싱만 확인, API 호출 없음
"""
import os, sys, time, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_embedding, EMBEDDING_DIMENSIONS
from scripts.lib.genre_parser import parse_genre

EMBED_BATCH = 20
SLEEP_BETWEEN = 1
MAX_CONSECUTIVE_ERRORS = 3


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def collect_unique_genres(sb):
    """books 테이블에서 고유 (genre_text, level) 쌍 수집."""
    genres = set()
    offset = 0
    while True:
        res = sb.table("books").select("genre") \
            .not_.is_("genre", "null").neq("genre", "") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            l1, l2 = parse_genre(row["genre"])
            if l1:
                genres.add((l1, "l1"))
            if l2:
                genres.add((l2, "l2"))
        if len(res.data) < 1000:
            break
        offset += 1000
    return genres


def get_existing(sb):
    """이미 genre_embeddings에 있는 (genre_text, level) 쌍."""
    existing = set()
    offset = 0
    while True:
        res = sb.table("genre_embeddings").select("genre_text, level") \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        for row in res.data:
            existing.add((row["genre_text"], row["level"]))
        if len(res.data) < 1000:
            break
        offset += 1000
    return existing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="파싱만 확인, API 호출 없음")
    args = parser.parse_args()

    sb = make_client()

    print("고유 장르 수집 중...", flush=True)
    all_genres = collect_unique_genres(sb)
    l1_count = sum(1 for _, level in all_genres if level == "l1")
    l2_count = sum(1 for _, level in all_genres if level == "l2")
    print(f"  고유 L1: {l1_count}개, L2: {l2_count}개, 합계: {len(all_genres)}개", flush=True)

    existing = get_existing(sb)
    todo = sorted(all_genres - existing)
    print(f"  이미 처리: {len(existing)}개, 남은 대상: {len(todo)}개", flush=True)

    if not todo:
        print("모든 장르가 이미 처리되었습니다.", flush=True)
        return

    if args.dry_run:
        print("\n[dry-run] 생성 대상 목록:")
        for text, level in todo:
            print(f"  [{level}] {text}")
        return

    # 사전 테스트 (1건)
    test_text, test_level = todo[0]
    print(f"\n사전 테스트: [{test_level}] {test_text}", flush=True)
    try:
        test_emb = call_embedding([test_text])
        assert len(test_emb) == 1 and len(test_emb[0]) == EMBEDDING_DIMENSIONS
        print(f"  ✓ 임베딩 성공 (dim={len(test_emb[0])})", flush=True)
    except Exception as e:
        print(f"  ✗ 사전 테스트 실패: {e}", flush=True)
        print("  배치를 시작하지 않습니다.", flush=True)
        sys.exit(1)

    # 배치 처리
    start = time.time()
    done, errors, consecutive_errors = 0, 0, 0

    for i in range(0, len(todo), EMBED_BATCH):
        batch = todo[i:i + EMBED_BATCH]
        texts = [text for text, _ in batch]

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
        for (text, level), emb in zip(batch, embeddings):
            rows.append({
                "genre_text": text,
                "level": level,
                "embedding": emb,
            })

        try:
            sb.table("genre_embeddings").insert(rows).execute()
            done += len(rows)
            consecutive_errors = 0
        except Exception as e:
            print(f"  배치 INSERT 실패, 1건씩 재시도: {e}", flush=True)
            for row in rows:
                try:
                    sb.table("genre_embeddings").insert(row).execute()
                    done += 1
                except Exception as e2:
                    errors += 1
                    print(f"    ✗ [{row['level']}] {row['genre_text'][:30]}: {e2}", flush=True)
            consecutive_errors = 0

        pct = (i + len(batch)) / len(todo) * 100
        print(f"  [{pct:5.1f}%] {done}/{len(todo)} 완료, {errors} 에러", flush=True)
        time.sleep(SLEEP_BETWEEN)

    elapsed = time.time() - start
    print(f"\n{'='*50}", flush=True)
    print(f"장르 임베딩 완료: {done}건 저장, {errors}건 에러, {elapsed:.0f}초", flush=True)
    print(f"{'='*50}", flush=True)


if __name__ == "__main__":
    main()
