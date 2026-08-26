"""book_love_reasons 중 reason_embedding 이 NULL 인 v3 행을 채운다.

배경(2026-08-26 실측):
  v3 reason 46,934행 중 **8,991행이 임베딩 없이 저장**돼 있다(전부 2026-04-01~04-12,
  v3 초기 롤아웃 시기). 그 결과 **1,240권은 임베딩된 reason 이 하나도 없어** 추천의
  reason 축에서 통째로 빠져 있다.

  그리고 v3_reason_extract.py 의 스킵 판정이
      .select("book_id").eq("source", SOURCE_TAG)
  로 **행 존재만** 보기 때문에, 임베딩이 NULL 이어도 "처리 완료"로 분류돼 재실행해도
  영원히 복구되지 않는다. 즉 자연 치유가 불가능한 상태였다.

  reason 텍스트는 이미 있으므로 **LLM 추출 없이 임베딩만** 하면 된다(비용 미미).

사용법:
  python3 scripts/backfill_reason_embedding.py --dry-run --limit 20   # 대상 확인
  python3 scripts/backfill_reason_embedding.py --limit 20             # 소량 검증
  python3 scripts/backfill_reason_embedding.py                        # 전량
"""
from __future__ import annotations

import argparse
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO, ".env"))

from scripts.lib.openai_helpers import call_embedding  # noqa: E402

SOURCE_TAG = "v3_context_rich"
EMBED_BATCH = 100          # OpenAI 임베딩 배치
UPDATE_BATCH = 50          # DB 업데이트 배치
REQUEST_DELAY = 0.2


def make_client():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def fetch_missing(sb, limit=None):
    """reason_embedding 이 NULL 인 v3 행을 id/reason 만 가져온다(페이지네이션).

    supabase-py 기본 1000행 캡 — range 로 전량 조회한다.
    """
    out, offset, page = [], 0, 1000
    while True:
        res = (sb.table("book_love_reasons")
               .select("id, book_id, reason")
               .eq("source", SOURCE_TAG)
               .is_("reason_embedding", "null")
               .range(offset, offset + page - 1)
               .execute())
        rows = res.data or []
        out.extend(rows)
        if len(rows) < page or (limit and len(out) >= limit):
            break
        offset += page
    return out[:limit] if limit else out


def run(dry_run=False, limit=None):
    sb = make_client()
    print("🔍 임베딩 누락 reason 조회 중...", flush=True)
    rows = fetch_missing(sb, limit)
    total = len(rows)
    print(f"   대상 {total}건", flush=True)
    if total == 0:
        print("✅ 채울 대상 없음.")
        return 0

    books = len({r["book_id"] for r in rows})
    print(f"   해당 책 {books}권\n", flush=True)
    if dry_run:
        for r in rows[:5]:
            print(f"   (dry-run) {r['reason'][:60]}")
        print(f"\n(dry-run) {total}건 임베딩 예정 — DB 미변경")
        return 0

    done = failed = 0
    for i in range(0, total, EMBED_BATCH):
        chunk = rows[i:i + EMBED_BATCH]
        texts = [r["reason"] for r in chunk]
        try:
            embs = call_embedding(texts)
        except Exception as e:
            failed += len(chunk)
            print(f"  ✗ 임베딩 실패 ({i}~{i+len(chunk)}): {str(e)[:90]}", flush=True)
            continue
        # 개별 UPDATE — upsert 는 다른 컬럼(source/created_at)을 건드릴 수 있어 피한다.
        for r, emb in zip(chunk, embs):
            for attempt in range(3):
                try:
                    sb.table("book_love_reasons").update(
                        {"reason_embedding": emb}).eq("id", r["id"]).execute()
                    done += 1
                    break
                except Exception as e:
                    if attempt == 2:
                        failed += 1
                        print(f"  ✗ UPDATE 실패 {r['id']}: {str(e)[:70]}", flush=True)
                    else:
                        time.sleep(1.5 * (attempt + 1))
        print(f"  {min(i+EMBED_BATCH, total)}/{total} | 완료={done} 실패={failed}", flush=True)
        time.sleep(REQUEST_DELAY)

    print(f"\n{'='*50}\n백필 완료: 성공 {done} / 실패 {failed} / 전체 {total}\n{'='*50}")
    return 1 if done == 0 else 0


def main():
    p = argparse.ArgumentParser(description="v3 reason 임베딩 백필")
    p.add_argument("--dry-run", action="store_true", help="대상만 확인, DB 미변경")
    p.add_argument("--limit", type=int, default=None, help="처리할 최대 건수")
    args = p.parse_args()
    return run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main() or 0)
