"""큐레이션 테마 ↔ 책 의미 매칭 (book_embeddings tier2 활용).

배경(2026-08-27 실측): 활성 테마가 전부 keyword 타입이고
`library_keywords @> ARRAY[keyword]` 정확 일치로만 매칭한다.
→ 큐레이션에 노출 가능한 책이 **1,577 / 9,588 (16%)** 뿐이고, 노출 슬롯 2,796 을
1,577권이 나눠 갖는다(평균 1.77배 중복, 최대 14개 테마 중복).

tier2 임베딩(제목·저자·장르·내용 융합)으로 의미 매칭하면 후보가 **5,774권**(커버+tier2)
으로 3.7배가 된다. 이 테이블은 추천 서빙이 읽지 않으므로 **추천 품질에 영향이 없다**
(v3 서빙은 book_v3_vectors + book_love_reasons).

⚠️ 모델 일치 필수: tier2 는 **text-embedding-3-small(1536D)** 다. -3-large 로 테마를
   임베딩하면 공간이 달라 유사도가 0.05 수준으로 무너진다(실측으로 한 번 헛다리 짚음).

⚠️ SQL 이 아니라 여기서 계산한다: 단일 테마 순차스캔 실측 2.56s → 444개면 19분이고,
   hnsw 인덱스는 ~42MB 라 Free DB 잔여 용량을 넘는다. 행렬곱 한 번이면 즉시 끝난다.

사용법:
  python3 scripts/curation_semantic_match.py --dry-run   # 매칭 결과만 출력
  python3 scripts/curation_semantic_match.py             # DB 적재
  python3 scripts/curation_semantic_match.py --threshold 0.45
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np
import psycopg
from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
load_dotenv(os.path.join(REPO, ".env"))

from scripts.lib.retry import with_retry  # noqa: E402

# tier2 와 동일해야 한다 — scripts/tier2_embedder.py EMBEDDING_MODEL 참조
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
# 프로토타입 실측: 0.45+ 정확 / 0.35~0.40 느슨 / 0.30 이하 약함.
DEFAULT_THRESHOLD = float(os.getenv("CURATION_SEMANTIC_THRESHOLD", "0.40"))
MAX_PER_THEME = int(os.getenv("CURATION_SEMANTIC_MAX", "12"))
PAGE = 1000

PW = os.environ["SUPABASE_DB_PASSWORD"]
REF = os.environ.get("SUPABASE_PROJECT_REF") or re.sub(
    r"https?://", "", os.environ["SUPABASE_URL"]).split(".")[0]
DSN = (f"postgresql://postgres.{REF}:{PW}"
       f"@aws-1-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require")
MINU = "00000000-0000-0000-0000-000000000000"


def connect():
    for a in range(6):
        try:
            return psycopg.connect(DSN, connect_timeout=30)
        except Exception as e:
            print(f"  conn retry {a}: {str(e)[:60]}", flush=True)
            time.sleep(5)
    raise SystemExit("DB 연결 실패")


def parse_halfvec(raw) -> np.ndarray:
    b = bytes(raw)
    d = int.from_bytes(b[0:2], "big")
    return np.frombuffer(b[4:4 + d * 2], dtype=">f2").astype(np.float32)


def to_halfvec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


def embed(texts):
    """OpenAI 임베딩(배치). tier2 와 동일 모델·차원."""
    import urllib.request
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": EMBEDDING_MODEL, "input": texts}).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as f:
        return [d["embedding"] for d in json.load(f)["data"]]


def theme_text(t) -> str:
    """테마를 대표하는 짧은 텍스트 — 제목 + 키워드."""
    kw = (t.get("parameters") or {}).get("keyword") or ""
    return f"{t['title']} {kw}".strip()


def load_book_matrix(conn):
    """tier2 임베딩 + 커버 있는 책. (ids, 정규화 행렬, 제목맵)"""
    ids, vecs, titles = [], [], {}
    last = MINU
    while True:
        with conn.cursor(binary=True) as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute(
                "select e.book_id, e.embedding, b.title from book_embeddings e "
                "join books b on b.id = e.book_id "
                "where e.tier = 2 and b.cover_url is not null and e.book_id > %s::uuid "
                "order by e.book_id limit %s", (last, PAGE))
            rows = cur.fetchall()
        if not rows:
            break
        for bid, emb, title in rows:
            ids.append(str(bid)); vecs.append(parse_halfvec(emb)); titles[str(bid)] = title
        last = str(rows[-1][0])
        if len(rows) < PAGE:
            break
    M = np.stack(vecs)
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    return ids, M, titles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--limit", type=int, default=0, help="테마 수 제한(검증용)")
    args = ap.parse_args()

    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        cur.execute("select id, title, parameters, theme_embedding is not null "
                    "from curation_themes where is_active = true order by id"
                    + (f" limit {args.limit}" if args.limit else ""))
        themes = [{"id": r[0], "title": r[1], "parameters": r[2], "has_emb": r[3]}
                  for r in cur.fetchall()]
    print(f"활성 테마 {len(themes)}개", flush=True)
    if not themes:
        return 0

    # 1) 테마 임베딩 — 없는 것만 생성(embed-once)
    todo = [t for t in themes if not t["has_emb"]]
    print(f"임베딩 생성 대상 {len(todo)}개", flush=True)
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        embs = embed([theme_text(t) for t in chunk])
        for t, e in zip(chunk, embs):
            t["vec"] = np.array(e, dtype=np.float32)
        if not args.dry_run:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 0")
                for t in chunk:
                    cur.execute("update curation_themes set theme_embedding = %s::halfvec "
                                "where id = %s", (to_halfvec_literal(t["vec"]), t["id"]))
            conn.commit()
        print(f"  임베딩 {min(i+100, len(todo))}/{len(todo)}", flush=True)

    # 이미 있던 테마 임베딩 읽기
    need = [t for t in themes if "vec" not in t]
    if need:
        with conn.cursor(binary=True) as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("select id, theme_embedding from curation_themes "
                        "where id = any(%s) and theme_embedding is not null",
                        ([t["id"] for t in need],))
            got = {r[0]: parse_halfvec(r[1]) for r in cur.fetchall()}
        for t in need:
            if t["id"] in got:
                t["vec"] = got[t["id"]]

    themes = [t for t in themes if "vec" in t]
    print(f"벡터 확보 테마 {len(themes)}개", flush=True)

    # 2) 책 행렬
    print("tier2 책 벡터 로딩...", flush=True)
    ids, M, titles = load_book_matrix(conn)
    print(f"  {M.shape[0]}권 × {M.shape[1]}차원", flush=True)

    # 3) 행렬곱 한 번으로 전 테마 유사도
    Q = np.stack([t["vec"] for t in themes])
    Q /= (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-9)
    S = Q @ M.T                                   # (테마, 책)

    n_hit = 0
    updates = []
    for row, t in zip(S, themes):
        idx = np.argsort(-row)[:MAX_PER_THEME]
        picked = [(ids[i], float(row[i])) for i in idx if row[i] >= args.threshold]
        if picked:
            n_hit += 1
        updates.append((t, picked))
        if args.dry_run and picked:
            print(f"\n「{t['title']}」 ({len(picked)}권)")
            for bid, s in picked[:5]:
                print(f"   {s:.3f}  {titles[bid][:52]}")

    print(f"\n임계값 {args.threshold}: {n_hit}/{len(themes)} 테마가 매칭 확보", flush=True)
    if args.dry_run:
        print("(dry-run — DB 미변경)")
        return 0

    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        for t, picked in updates:
            cur.execute("update curation_themes set semantic_book_ids = %s, "
                        "semantic_updated_at = now() where id = %s",
                        (json.dumps([b for b, _ in picked]) if picked else None, t["id"]))
    conn.commit()
    print(f"✅ {len(updates)}개 테마 적재 완료")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
