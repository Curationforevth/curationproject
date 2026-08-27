"""직접연결 인덱스 재빌드 (Actions 전용).

목적: 무료 Supabase 가 PostgREST 로 대용량 벡터(9483×2000≈235MB)를 serving 하다 과부하
(57014/522)로 죽는 문제를 회피한다. v3 벡터와 reason 임베딩을 **직접 psycopg(pooler)** 로
keyset 읽는다(재임베딩 0, OpenAI 미사용). l1/l2 는 dead(W_L1=W_L2=0)라 zero.
출력은 v4-prestacked 번들.

⚠️ 2026-08-27 개정 — reason 을 DB 에서 읽는다:
  예전엔 reason 을 **기존 index.pkl 에서 재사용**했다. 그래서 DB 에서 새로 채워진
  reason 임베딩이 서빙 인덱스에 **영영 반영되지 않았다**. 임베딩 없이 저장됐던 v3
  reason 8,991건을 백필해 1,240권을 살렸는데, 이 경로를 고치지 않으면 그 성과가
  인덱스에 도달하지 못한다.
  전송량 때문에 **바이너리로 읽는다** — ::text 는 벡터당 25KB(전체 1.1GB egress),
  바이너리는 4KB(179MB). 실측 재구성 오차 0.
  그리고 **source='v3_context_rich' 필터 필수** — v2 유물 llm_extracted 33,858행
  (조각 키워드)이 섞이면 추천 품질이 오염된다.

env: SUPABASE_DB_PASSWORD, SUPABASE_PROJECT_REF(또는 SUPABASE_URL) 등.
     INDEX_OUT 을 주면 그 경로로 쓴다(검증용 — 프로덕션 pkl 미변경).

env: SUPABASE_DB_PASSWORD, SUPABASE_PROJECT_REF(또는 SUPABASE_URL), + config 용
SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/OPENAI_API_KEY.
"""
import os, re, sys, time, pickle, hashlib
from datetime import datetime, timezone
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg
from engine.index import VectorIndex
from config import EMBEDDING_DIMENSIONS as D

SRC = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")
OUT = os.environ.get("INDEX_OUT") or SRC
PW = os.environ["SUPABASE_DB_PASSWORD"]
REF = os.environ.get("SUPABASE_PROJECT_REF") or re.sub(r"https?://", "", os.environ["SUPABASE_URL"]).split(".")[0]
DSN = f"postgresql://postgres.{REF}:{PW}@aws-1-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require"
MINU = "00000000-0000-0000-0000-000000000000"


def to_np(s):
    if isinstance(s, str):
        s = s.strip().strip("[]")
        v = [float(x) for x in s.split(",")] if s else []
    else:
        v = s
    a = np.array(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


def connect():
    for a in range(40):
        try:
            return psycopg.connect(DSN, connect_timeout=20)
        except Exception as e:
            print(f"conn retry {a}: {str(e)[:60]}", flush=True); time.sleep(10)
    raise SystemExit("no db connection")


REASON_SOURCE = "v3_context_rich"


def parse_halfvec(raw) -> np.ndarray:
    """pgvector halfvec 바이너리 → float32 벡터.

    포맷: dim(int16 BE) + unused(int16) + dim 개의 big-endian float16.
    ::text 로 읽으면 벡터당 ~25KB(전체 1.1GB egress)인데 바이너리는 4KB(179MB)다.
    실측 재구성 오차 0 — 정확 일치.
    """
    b = bytes(raw)
    dim = int.from_bytes(b[0:2], "big")
    return np.frombuffer(b[4:4 + dim * 2], dtype=">f2").astype(np.float32)


def read_reasons(conn, page=500):
    """book_love_reasons 의 v3 reason 임베딩을 book_id 별로 모아 반환.

    ⚠️ source 필터 필수 — v2 유물 `llm_extracted` 33,858행("현직 교사 집필" 같은
    조각 키워드)이 섞이면 추천 품질이 오염된다. 임베딩이 없는 행도 제외한다.

    keyset 은 **uuid 컬럼 id** 로 정렬한다. id::text 로 정렬하면 텍스트 캐스트
    때문에 전량 sort 가 걸린다(실측 20건에 21.9초 → statement timeout).
    """
    sql = (f"select id, book_id, reason_embedding from book_love_reasons "
           f"where source = '{REASON_SOURCE}' and reason_embedding is not null "
           f"and id > %s::uuid order by id limit {page}")
    by_book: dict[str, list] = {}
    last, n = MINU, 0
    while True:
        try:
            with conn.cursor(binary=True) as cur:
                cur.execute("set statement_timeout=0")
                cur.execute(sql, (last,))
                rows = cur.fetchall()
        except Exception as e:
            print(f"  reasons read err, reconnect: {str(e)[:60]}", flush=True)
            try: conn.close()
            except Exception: pass
            conn = connect(); continue
        if not rows:
            break
        for _rid, bid, emb in rows:
            by_book.setdefault(str(bid), []).append(parse_halfvec(emb))
        n += len(rows)
        last = str(rows[-1][0])
        if n % 5000 < page:
            print(f"  reasons: {n}", flush=True)
        if len(rows) < page:
            break
    print(f"  reasons: {n} (책 {len(by_book)}권)", flush=True)
    return by_book, conn


def keyset(conn, sql, label, page):
    out = []
    last = MINU
    while True:
        try:
            cur = conn.cursor(); cur.execute(sql, (last,)); rows = cur.fetchall()
        except Exception as e:
            print(f"{label} read err, reconnect: {str(e)[:60]}", flush=True)
            try: conn.close()
            except Exception: pass
            conn = connect(); continue
        if not rows:
            break
        out.extend(rows); last = rows[-1][0]
        print(f"  {label}: {len(out)}", flush=True)
        if len(rows) < page:
            break
    return out, conn


def main():
    print("[direct-build] loading old pkl (meta 재사용)...", flush=True)
    old = pickle.load(open(SRC, "rb"))
    old_pre = old.get("prestacked_reasons_f16") or {}
    old_meta = old.get("meta") or {}
    print(f"  old: {len(old.get('bid_order') or [])} books, {sum(int(v.shape[0]) for v in old_pre.values())} reasons", flush=True)

    conn = connect()
    print("[direct-build] reading reasons from DB (binary keyset)...", flush=True)
    db_reasons, conn = read_reasons(conn)
    print("[direct-build] reading v3 vectors (direct keyset)...", flush=True)
    v3, conn = keyset(conn,
        "select book_id::text, desc_embedding::text, source_tier from book_v3_vectors "
        "where book_id > %s::uuid order by book_id limit 500", "v3", 500)
    print("[direct-build] reading books meta (direct keyset)...", flush=True)
    mrows, conn = keyset(conn,
        "select id::text, title, author, cover_url from books "
        "where id > %s::uuid order by id limit 1000", "meta", 1000)
    conn.close()

    meta = dict(old_meta)
    for r in mrows:
        meta[r[0]] = {"title": r[1], "author": r[2], "cover_url": r[3]}

    # 안전장치: DB reason 이 기존 pkl 보다 크게 줄었으면 읽기 사고를 의심하고 중단한다.
    old_total = sum(int(v.shape[0]) for v in old_pre.values())
    new_total = sum(len(v) for v in db_reasons.values())
    if old_total and new_total < old_total * 0.9:
        print(f"❌ reasons 급감 {old_total} → {new_total} (10% 초과 감소) — 읽기 사고 의심, abort",
              file=sys.stderr)
        sys.exit(1)
    print(f"[direct-build] building index ({len(v3)} books, reasons {old_total}→{new_total} from DB, l1/l2 zero)...", flush=True)
    idx = VectorIndex(dim=D, dtype=np.float16)
    tier_map = {}
    z = np.zeros(D, dtype=np.float32)
    for bid, desc, tier in v3:
        reasons = db_reasons.get(bid) or []
        idx.add_book(bid, reasons=reasons, desc=to_np(desc), l1=z, l2=z)
        if tier and tier != "rich":
            tier_map[bid] = tier
    idx._candidate_tier = tier_map
    bid_order = list(idx._books.keys())

    prestacked = {}; agg = []; descs = []
    for bid in bid_order:
        bv = idx.get_book(bid); descs.append(bv.desc)
        if bv.reasons:
            st = np.stack(bv.reasons).astype(np.float16); prestacked[bid] = st
            m = np.mean(st.astype(np.float32), axis=0); n = np.linalg.norm(m)
            agg.append((m / n).astype(np.float16) if n > 0 else m.astype(np.float16))
        else:
            prestacked[bid] = np.empty((0, D), dtype=np.float16); agg.append(np.zeros(D, dtype=np.float16))
    desc_matrix = np.stack(descs).astype(np.float16)
    agg_matrix = np.stack(agg)
    total_reasons = sum(int(v.shape[0]) for v in prestacked.values())
    if total_reasons == 0:
        print("❌ reasons 0 — abort", file=sys.stderr); sys.exit(1)

    for bid in bid_order:
        bv = idx.get_book(bid)
        bv.reasons = []
        bv.desc = None  # desc dedup: 번들 desc_matrix_f16 하나만 보유 → 로드 시 attach
    idx.strip_unused_genre_vectors()

    bundle = {
        "index": idx, "meta": meta, "built_at": datetime.now(timezone.utc).isoformat(),
        "version": "v4-prestacked", "prestacked_reasons_f16": prestacked,
        "desc_matrix_f16": desc_matrix, "agg_reason_matrix_f16": agg_matrix, "bid_order": bid_order,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(bundle, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, OUT)
    sha = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    nonrich = {}
    for t in tier_map.values():
        nonrich[t] = nonrich.get(t, 0) + 1
    print(f"[direct-build] DONE: {len(bid_order)} books, {total_reasons} reasons, "
          f"non-rich={nonrich}, size={os.path.getsize(OUT)/1024/1024:.0f}MB, sha={sha[:12]}", flush=True)


if __name__ == "__main__":
    main()
