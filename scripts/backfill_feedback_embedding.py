"""
유저 리뷰 임베딩 backfill — user_books.feedback_embedding 채우기.

/feedback 요청경로에서 OpenAI 동기호출을 제거(api/feedback.py:_embed_and_recompute,
BackgroundTask)했으므로, 임베딩이 실패한(=feedback_embedding NULL 인데 review_text 는
있는) 행을 이 배치가 다음 run 에 채워 신호 소실을 0 으로 만든다.
([[feedback_accumulate_not_realtime_api]] — DB 축적 후 배치 처리, 실패 재시도.)

모델/차원은 추천 스코어링과 반드시 일치: text-embedding-3-large, 2000D(Matryoshka).
(api/feedback.py·config.py·book_v3_vectors·book_love_reasons 와 동일. small(1536)을
쓰면 desc/reason 벡터와 차원 불일치로 스코어링 불가.)

사용법:
  python3 scripts/backfill_feedback_embedding.py            # 미처리분 전체
  python3 scripts/backfill_feedback_embedding.py --limit 200
  python3 scripts/backfill_feedback_embedding.py --dry-run  # 대상만 집계, 쓰기/임베딩 없음
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 2000
BATCH = 64
PAGE = 500
MAX_RETRIES = 3
RETRY_BACKOFF = 10
PAGE_SLEEP = 0.3


def _fb_text(emotion_tags, review_text):
    """감정태그 + 한줄감상을 임베딩 입력으로 조립(무절단). 라이브 경로
    engine/user_embed.build_feedback_text 와 동일 로직(scripts→engine import 불가라 복제)."""
    tags = [t for t in (emotion_tags or []) if t and str(t).strip()]
    review = (review_text or "").strip()
    if not tags and not review:
        return None
    parts = []
    if tags:
        parts.append("태그: " + ", ".join(tags))
    if review:
        parts.append(review)
    return "\n".join(parts)


def needs_embedding(row: dict) -> bool:
    """태그 또는 한줄감상 내용이 있고 feedback_embedding 이 비어있으면 backfill 대상."""
    return _fb_text(row.get("emotion_tags"), row.get("review_text")) is not None \
        and not row.get("feedback_embedding")


def fetch_targets(sb, limit: int) -> list[dict]:
    """feedback_embedding NULL 인 행 중 태그/리뷰 있는 것 조회(페이지네이션).

    태그 OR 리뷰 조건은 PostgREST 단일 필터로 표현이 까다로워, 임베딩 NULL 만
    서버 필터하고 태그/리뷰 유무는 needs_embedding 으로 파이썬 필터한다.
    """
    out: list[dict] = []
    offset = 0
    while True:
        end = offset + PAGE - 1
        res = with_retry(lambda o=offset, e=end: sb.table("user_books")
                         .select("user_id, book_id, review_text, emotion_tags")
                         .is_("feedback_embedding", "null")
                         .range(o, e)
                         .execute())
        rows = res.data or []
        out.extend(r for r in rows if needs_embedding(r))
        if len(rows) < PAGE:
            break
        offset += PAGE
        if limit and len(out) >= limit:
            break
        time.sleep(PAGE_SLEEP)
    return out[:limit] if limit else out


def embed_batch(texts: list[str]) -> list[list[float]]:
    key = os.environ["OPENAI_API_KEY"]
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": EMBEDDING_MODEL, "input": chunk,
                          "dimensions": EMBEDDING_DIMENSIONS},
                    timeout=60,
                )
                resp.raise_for_status()
                out.extend(d["embedding"] for d in resp.json()["data"])
                break
            except Exception as e:
                print(f"  [embed retry {attempt}/{MAX_RETRIES}] {e}", flush=True)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BACKOFF * attempt)
        time.sleep(0.3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"],
                       os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    targets = fetch_targets(sb, args.limit)
    print(f"[backfill] feedback_embedding 대상(review 있음 + 임베딩 NULL): {len(targets)}",
          flush=True)
    if not targets:
        print("[backfill] 대상 없음 — 종료")
        return
    if args.dry_run:
        print(f"[dry-run] {len(targets)}건 임베딩/쓰기 건너뜀. 예시:")
        for r in targets[:3]:
            txt = _fb_text(r.get("emotion_tags"), r.get("review_text"))
            print(f"  u={r['user_id'][:8]} b={r['book_id'][:8]} text={txt[:40]!r}")
        return

    texts = [_fb_text(r.get("emotion_tags"), r.get("review_text")) for r in targets]
    embs = embed_batch(texts)
    assert len(embs) == len(targets), f"임베딩 수 불일치 {len(embs)} != {len(targets)}"

    done = errors = 0
    for r, emb in zip(targets, embs):
        try:
            with_retry(lambda r=r, emb=emb: sb.table("user_books")
                       .update({"feedback_embedding": emb})
                       .eq("user_id", r["user_id"]).eq("book_id", r["book_id"])
                       .execute())
            done += 1
        except Exception as e:
            errors += 1
            print(f"  [update 실패] u={r['user_id'][:8]} b={r['book_id'][:8]}: {e}", flush=True)
    print(f"[backfill] 완료: {done}건 채움, {errors}건 에러", flush=True)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
