"""provisional(non-rich) book_v3_vectors 행을 재도출 — tier 라벨 교정 + rich 승격 재임베딩.

근거: 후보풀 커버리지 설계(2026-06-28-candidate-pool-coverage-design) C5.
- 마이그레이션이 기존 provisional 행을 'kakao_desc' 로 임시 backfill → 실제 minimal 행이
  오라벨됨. 이 배치가 build_desc_source 로 정확 tier 를 재도출해 교정한다.
- 나중에 YES24/알라딘이 rich_description 을 채우면 rich 로 승격(재임베딩).
- embed-once: source_text 가 바뀐 경우에만 OpenAI 재임베딩. tier 만 바뀌면 라벨만 UPDATE.

모델/차원은 추천 스코어링과 일치: text-embedding-3-large, 2000D.

사용법:
  python3 scripts/reembed_provisional.py            # 미rich 전체
  python3 scripts/reembed_provisional.py --limit 300
  python3 scripts/reembed_provisional.py --dry-run  # 대상/액션 집계만(임베딩·쓰기 없음)
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
PAGE = 500
BATCH = 64
MAX_RETRIES = 3
RETRY_BACKOFF = 10
PAGE_SLEEP = 0.3


def plan_row_action(stored_tier, stored_source_text, new_text, new_tier):
    """순수 결정: 한 행을 어떻게 처리할지.

    - new_text 가 없거나(텍스트 소실) new_tier None → 아무것도 안 함(기존 보존).
    - source_text 가 바뀌면 재임베딩(OpenAI). 안 바뀌면 재임베딩 skip(embed-once).
    - 재도출 tier 가 저장 tier 와 다르면 항상 라벨 UPDATE(R3 — source_text 불변이어도
      backfill 임시라벨('kakao_desc'→실제 'minimal') 교정).
    """
    if not new_text or new_tier is None:
        return {"reembed": False, "update_tier": False, "new_tier": stored_tier}
    return {
        "reembed": new_text != stored_source_text,
        "update_tier": new_tier != stored_tier,
        "new_tier": new_tier,
    }


def fetch_provisional(sb, limit):
    """source_tier != 'rich' 인 book_v3_vectors 행 조회(페이지네이션)."""
    out = []
    offset = 0
    while True:
        res = with_retry(lambda o=offset: sb.table("book_v3_vectors")
                         .select("book_id, source_text, source_tier")
                         .neq("source_tier", "rich")
                         .range(o, o + PAGE - 1).execute())
        rows = res.data or []
        out.extend(rows)
        if len(rows) < PAGE:
            break
        offset += PAGE
        if limit and len(out) >= limit:
            break
        time.sleep(PAGE_SLEEP)
    return out[:limit] if limit else out


def fetch_books(sb, book_ids):
    """대상 책의 books 메타(재도출용). id → row."""
    by_id = {}
    for i in range(0, len(book_ids), PAGE):
        chunk = book_ids[i:i + PAGE]
        res = with_retry(lambda c=chunk: sb.table("books")
                         .select("id, title, author, genre, description, rich_description")
                         .in_("id", c).execute())
        for b in (res.data or []):
            by_id[b["id"]] = b
        time.sleep(PAGE_SLEEP)
    return by_id


def _embed_one(text):
    key = os.environ["OPENAI_API_KEY"]
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": EMBEDDING_MODEL, "input": [text], "dimensions": EMBEDDING_DIMENSIONS},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except Exception as e:
            print(f"  [embed retry {attempt}/{MAX_RETRIES}] {e}", flush=True)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF * attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from generate_book_v3_vectors import build_desc_source  # lazy(체인 import 회피)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    rows = fetch_provisional(sb, args.limit)
    print(f"[reembed] provisional(non-rich) 대상: {len(rows)}", flush=True)
    if not rows:
        print("[reembed] 대상 없음 — 종료")
        return

    books = fetch_books(sb, [r["book_id"] for r in rows])

    n_reembed = n_relabel = n_noop = errors = 0
    for r in rows:
        bid = r["book_id"]
        book = books.get(bid)
        if not book:
            continue
        new_text, new_tier = build_desc_source(book)
        action = plan_row_action(r.get("source_tier"), r.get("source_text"),
                                 new_text, new_tier)
        if not action["reembed"] and not action["update_tier"]:
            n_noop += 1
            continue
        try:
            if args.dry_run:
                tag = "reembed" if action["reembed"] else "relabel"
                print(f"  [{tag}] b={bid[:8]} {r.get('source_tier')}→{action['new_tier']}",
                      flush=True)
            elif action["reembed"]:
                emb = _embed_one(new_text)
                with_retry(lambda: sb.table("book_v3_vectors").upsert({
                    "book_id": bid, "desc_embedding": emb, "source_text": new_text[:2000],
                    "source_tier": action["new_tier"],
                    "provisional": action["new_tier"] != "rich",
                }, on_conflict="book_id").execute())
                n_reembed += 1
            else:  # update_tier only (라벨 교정, OpenAI 0)
                with_retry(lambda: sb.table("book_v3_vectors").update({
                    "source_tier": action["new_tier"],
                    "provisional": action["new_tier"] != "rich",
                }).eq("book_id", bid).execute())
                n_relabel += 1
            time.sleep(0.2)
        except Exception as e:
            errors += 1
            print(f"  [실패] b={bid[:8]}: {e}", flush=True)

    print(f"[reembed] 완료: 재임베딩 {n_reembed}, 라벨교정 {n_relabel}, "
          f"변경없음 {n_noop}, 에러 {errors}", flush=True)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
