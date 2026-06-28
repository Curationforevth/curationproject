# User Taste Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 유저가 고른 어떤 책에서든(인덱스에 없어도) 취향을 추출해 같은 세션 추천에 반영하고, 수집 중이지만 버려지던 피드백(감정태그+한줄감상)을 라이브 취향에 넣는다.

**Architecture:** 서버 recommendation-server만 수정(앱 변경 0). ① 유저 책을 가용 텍스트로 embed-once하여 `book_v3_vectors`에 축적(C1) ② 인덱스 밖 좋아요 책의 벡터를 DB에서 읽어 2-stage 스코어러에 *쿼리 벡터로 주입*(C2, 후보풀은 정적 인덱스 유지) ③ recompute가 감정태그+리뷰를 임베딩해 `feedback_embedding` 채움(C3) ④ 캐시미스 시 미임베딩 책/피드백이 있으면 inline 성공 여부와 무관하게 백그라운드 recompute를 큐잉하고, recompute는 임베딩→재read→post-embedding hash→스코어링 순서로 코히런스 유지(C4).

**Tech Stack:** Python 3 / FastAPI, Supabase (PostgreSQL + pgvector), OpenAI text-embedding-3-large(2000D), numpy. pytest.

설계 정본: `docs/superpowers/specs/2026-06-28-user-taste-extraction-design.md` (3라운드 리뷰 수렴).

## Global Constraints

- **앱 변경 0.** recommendation-server만 수정한다. 앱은 이미 `rating/emotion_tags/review_text`를 Supabase에 직접 쓴다.
- **OpenAI는 백그라운드 recompute(또는 배치)에서만.** 요청경로·inline 경로에 OpenAI 호출 추가 금지([[project_perf_freetier]]).
- **embed-once 축적.** 이미 `book_v3_vectors`에 있으면 재임베딩 금지(OpenAI 0회)([[feedback_accumulate_not_realtime_api]]).
- **임베딩 모델/차원 고정:** `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS=2000`(config.py). 벡터는 L2 정규화(`engine/utils.py:to_np`).
- **후보풀(추천 대상)은 정적 인덱스 그대로.** 유저 책은 취향 소스로만(후보 편입은 범위 밖).
- **자동배포 주의:** 이 레포는 main 머지 시 recommendation-server가 prod로 배포됨([[project_repo_autocommit_deploy]]). **전 작업은 feature 브랜치에서, 로컬 pytest green 후 Eden 승인 머지.** push/머지 금지(명시 승인 시만). prod E2E는 머지·배포 후.
- **마이그레이션은 PR 머지 시 apply-migrations 자동 적용**([[feedback_no_direct_sql]]). 직접 prod SQL 금지.
- **벡터 신호 독립(P3)·책 맥락 유지(P1)·취향=스펙트럼(P5):** per-book max-sim 유지, centroid(np.average) 금지.

---

## File Structure

- **Create** `recommendation-server/engine/user_embed.py` — 유저 책 임베딩(C1) + 인덱스 밖 책 벡터 resolve(C2 헬퍼) + 피드백 텍스트 조립/임베딩(C3). 한 책임: "DB의 유저 책/피드백을 스코어링 가능한 벡터로 만든다".
- **Modify** `recommendation-server/engine/twostage.py` — `stage1_hybrid`, `batch_score_prestacked`에 `extra_query` 인자(C2).
- **Modify** `recommendation-server/engine/recommend_core.py` — `compute_scored_books`, `try_compute_inline`에 `extra_query` 통과(C2 inline).
- **Modify** `recommendation-server/engine/cache.py` — `recompute_recommendations` 통합(C1+C3+C2, embed-first, post-embedding hash), computing 플래그 no-blank.
- **Modify** `recommendation-server/api/recommend.py` — SELECT 확장, 트리거 술어, extra_query, queue+skip(C4).
- **Modify** `recommendation-server/api/home.py` — 동일(C4, 두 번째 사이트).
- **Modify** `recommendation-server/scripts/backfill_feedback_embedding.py` → 실제 경로 `scripts/backfill_feedback_embedding.py` — 태그 포함(C3 배치 동기화).
- **Create** `supabase/migrations/20260628000000_book_v3_vectors_provisional.sql` — `provisional` 컬럼.
- **Test** `recommendation-server/tests/test_user_embed.py`, `tests/test_twostage_augment.py`, `tests/test_recompute_integration.py`.

---

## Task 1: Migration — `book_v3_vectors.provisional`

**Files:**
- Create: `supabase/migrations/20260628000000_book_v3_vectors_provisional.sql`

**Interfaces:**
- Produces: `book_v3_vectors.provisional BOOLEAN DEFAULT FALSE` (C1이 얕은 텍스트 임베딩 시 TRUE 표시; 후속 보강 대상).

- [ ] **Step 1: Write the migration**

```sql
-- 유저가 추가한 책을 가용 텍스트(카카오 contents 등)로 임시 임베딩한 경우 TRUE.
-- 후속 보강 배치가 rich_description 확보 후 재임베딩할 대상을 식별한다.
ALTER TABLE book_v3_vectors
  ADD COLUMN IF NOT EXISTS provisional BOOLEAN NOT NULL DEFAULT FALSE;
```

- [ ] **Step 2: Verify it parses (dry-run, no prod write)**

Run: `cd "recommendation-server" 2>/dev/null; python3 -c "import pathlib,re; s=pathlib.Path('../supabase/migrations/20260628000000_book_v3_vectors_provisional.sql').read_text(); assert 'provisional' in s and 'IF NOT EXISTS' in s; print('ok')"`
Expected: `ok` (실제 적용은 PR 머지 시 apply-migrations. dry-run은 DDL 검증 못 하므로 실적용은 머지로만).

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260628000000_book_v3_vectors_provisional.sql
git commit -m "feat: book_v3_vectors.provisional 컬럼 (유저 책 임시 임베딩 표시)"
```

---

## Task 2: C2 — 스코어러 augmentation (`twostage.py`)

`stage1_hybrid`/`batch_score_prestacked`가 정적 인덱스 밖 좋아요 책의 벡터를 *쿼리*로 받도록 확장. desc 항만 주입(fb는 이미 `fb_data` 루프가 무가드로 처리 — 이중계산 금지). 후보풀 불변.

**Files:**
- Modify: `recommendation-server/engine/twostage.py`
- Test: `recommendation-server/tests/test_twostage_augment.py`

**Interfaces:**
- Consumes: `BookVectors`(engine/index.py — `reasons:list, desc:ndarray, l1:ndarray, l2:ndarray`).
- Produces:
  - `stage1_hybrid(liked_books, fb_data, desc_matrix_f16, agg_reason_matrix_f16, bid_order, top_n=700, extra_query: dict[str, BookVectors] | None = None) -> list[str]`
  - `batch_score_prestacked(index, liked_books, fb_data, candidate_ids, prestacked_reasons, ..., extra_query: dict[str, BookVectors] | None = None) -> dict`
  - `extra_query`는 `bid_to_idx`에 *없는* 좋/싫 book_id만 담는다(인덱스에 있으면 기존 경로).

- [ ] **Step 1: Write failing test — 인덱스 밖 good 책만으로도 stage1이 후보를 낸다**

```python
# recommendation-server/tests/test_twostage_augment.py
import numpy as np
from engine.index import BookVectors
from engine.twostage import stage1_hybrid, batch_score_prestacked


def _unit(seed, dim=2000):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_stage1_returns_candidates_when_all_good_books_out_of_index():
    dim = 2000
    bid_order = ["c1", "c2", "c3"]
    dm = np.stack([_unit(1, dim), _unit(2, dim), _unit(3, dim)]).astype(np.float16)
    am = np.zeros((3, dim), dtype=np.float16)
    liked = {"USER_BOOK": {"rating": "good"}}  # not in bid_order
    extra = {"USER_BOOK": BookVectors(reasons=[], desc=dm[0].astype(np.float32),
                                      l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    out = stage1_hybrid(liked, {}, dm, am, bid_order, top_n=3, extra_query=extra)
    assert out, "인덱스 밖 good 책 주입 시에도 후보가 나와야 함"
    assert "c1" in out  # USER_BOOK desc == c1 desc → 최상위
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py::test_stage1_returns_candidates_when_all_good_books_out_of_index -v`
Expected: FAIL (`stage1_hybrid() got an unexpected keyword argument 'extra_query'` 또는 빈 `[]` 반환).

- [ ] **Step 3: Implement `extra_query` in `stage1_hybrid`**

`stage1_hybrid` 시그니처에 `extra_query=None` 추가. 함수 상단 `good_ids/bad_ids/read_ids` 계산 직후에 주입 벡터 분리:

```python
    extra_query = extra_query or {}
    extra_good = [bid for bid in good_ids if bid in extra_query]
    extra_bad = [bid for bid in bad_ids if bid in extra_query]
```

`good_desc_indices` early-exit를 확장(기존 `if not good_desc_indices: return []`):

```python
    good_desc_indices = [bid_to_idx[bid] for bid in good_ids if bid in bid_to_idx]
    if not good_desc_indices and not extra_good:
        return []
```

`good_descs`/`good_aggs` 빌드 시 주입 desc(f32)와 zero agg를 vstack:

```python
    idx_descs = dm[good_desc_indices] if good_desc_indices else np.zeros((0, dm.shape[1]), np.float32)
    idx_aggs = am[good_desc_indices] if good_desc_indices else np.zeros((0, am.shape[1]), np.float32)
    if extra_good:
        ex_descs = np.stack([extra_query[b].desc.astype(np.float32) for b in extra_good])
        ex_aggs = np.zeros((len(extra_good), am.shape[1]), np.float32)  # 유저 책 reason 없음 → 0
        good_descs = np.vstack([idx_descs, ex_descs])
        good_aggs = np.vstack([idx_aggs, ex_aggs])
    else:
        good_descs = idx_descs
        good_aggs = idx_aggs
    if good_descs.shape[0] == 0:
        return []
```

(기존 `good_descs = dm[good_desc_indices]` / `good_aggs = am[good_desc_indices]` 두 줄을 위 블록으로 교체.)

per-book `pb_scores` 루프에 주입 **desc 항만** 추가(기존 good/bad 루프 바로 뒤, fb 루프 앞):

```python
    for bid in extra_good:
        pb_scores += 3.0 * (dm @ extra_query[bid].desc.astype(np.float32))
    for bid in extra_bad:
        pb_scores -= 1.5 * (dm @ extra_query[bid].desc.astype(np.float32))
    # 주의: fb 항은 추가하지 않는다 — 아래 fb_data 루프가 bid_to_idx 가드 없이
    # 인덱스 밖 책의 fb 도 이미 full-weight 반영(이중계산 방지).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py::test_stage1_returns_candidates_when_all_good_books_out_of_index -v`
Expected: PASS

- [ ] **Step 5: Write failing test — `batch_score_prestacked`가 주입 BookVectors를 query로 사용**

```python
def test_batch_score_uses_extra_query_good_book():
    from engine.index import VectorIndex
    dim = 2000
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim), l1=np.zeros(dim), l2=np.zeros(dim))
    liked = {"USER_BOOK": {"rating": "good"}}
    extra = {"USER_BOOK": BookVectors(reasons=[], desc=_unit(1, dim),
                                      l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    scores = batch_score_prestacked(idx, liked, {}, ["cand"], {}, extra_query=extra)
    assert "cand" in scores
    assert scores["cand"] > 0  # USER_BOOK desc ≈ cand desc → desc_score 양수
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py::test_batch_score_uses_extra_query_good_book -v`
Expected: FAIL (unexpected kwarg `extra_query` 또는 빈 scores).

- [ ] **Step 7: Implement `extra_query` in `batch_score_prestacked`**

시그니처에 `extra_query=None` 추가. `good_books` 빌드(기존 `good_books = {bid: index.get_book(bid) for bid in good_ids}`)를 주입 fallback으로 교체:

```python
    extra_query = extra_query or {}
    good_books = {bid: (index.get_book(bid) or extra_query.get(bid)) for bid in good_ids}
    good_books = {bid: bv for bid, bv in good_books.items() if bv is not None}
```

bad 루프의 `bv = index.get_book(bid)`도 `bv = index.get_book(bid) or extra_query.get(bid)`로 교체. (l1/l2 zero는 `W_L1=W_L2=0` 가드로 무해; reasons=[] → r_sim=0.)

- [ ] **Step 8: Run test to verify it passes**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py::test_batch_score_uses_extra_query_good_book -v`
Expected: PASS

- [ ] **Step 9: Regression — extra_query 없이 기존 동작 동일**

Run: `cd recommendation-server && python -m pytest tests/ -k "twostage or stage1 or prestacked" -v`
Expected: 기존 테스트 전부 PASS(시그니처 변경 하위호환 = 기본 None).

- [ ] **Step 10: Commit**

```bash
git add recommendation-server/engine/twostage.py recommendation-server/tests/test_twostage_augment.py
git commit -m "feat: 2-stage 스코어러 extra_query 주입 (인덱스 밖 책 취향 반영, desc만)"
```

---

## Task 3: C1/C2 헬퍼 — `user_embed.py` (resolve + embed)

**Files:**
- Create: `recommendation-server/engine/user_embed.py`
- Test: `recommendation-server/tests/test_user_embed.py`

**Interfaces:**
- Consumes: `config.get_supabase`, `config.EMBEDDING_MODEL/EMBEDDING_DIMENSIONS`, `engine/index.BookVectors`, `engine/utils.to_np`.
- Produces:
  - `resolve_extra_query_vectors(liked_ids: list[str], bid_order_set: set[str], sb) -> dict[str, BookVectors]` — 인덱스(`bid_order_set`)에 *없는* book_id만 `book_v3_vectors`에서 읽어 BookVectors 합성(OpenAI 없음).
  - `ensure_books_embedded(book_ids: list[str], sb, embed_fn=None) -> None` — `book_v3_vectors`에 없는 책을 가용 텍스트로 embed-once 저장. `embed_fn`(주입 가능, 기본 OpenAI)으로 테스트 격리.
  - `_pick_source_text(book_row: dict) -> tuple[str, bool]` — (텍스트, provisional). rich≥200 → (rich, False), else description → (desc, True), else title+author+genre → (..., True).

- [ ] **Step 1: Write failing test — 텍스트 우선순위 + provisional**

```python
# recommendation-server/tests/test_user_embed.py
from engine.user_embed import _pick_source_text


def test_pick_source_prefers_rich_when_long():
    row = {"rich_description": "가"*250, "description": "짧은", "title": "T", "author": "A", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert text == "가"*250 and prov is False


def test_pick_source_falls_back_to_description():
    row = {"rich_description": "짧음", "description": "카카오 줄거리 문단입니다.", "title": "T", "author": "A", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert text == "카카오 줄거리 문단입니다." and prov is True


def test_pick_source_last_resort_title_author_genre():
    row = {"rich_description": None, "description": None, "title": "어린왕자", "author": "생텍쥐페리", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert "어린왕자" in text and "생텍쥐페리" in text and "소설" in text and prov is True
```

- [ ] **Step 2: Run to verify fail**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `_pick_source_text` + module skeleton**

```python
# recommendation-server/engine/user_embed.py
from __future__ import annotations
import logging
import numpy as np
import requests
from config import (get_supabase, OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
from engine.index import BookVectors
from engine.utils import to_np

logger = logging.getLogger(__name__)
_MIN_RICH = 200


def _pick_source_text(row: dict) -> tuple[str, bool]:
    """(임베딩 텍스트, provisional). rich≥200 우선, 그다음 카카오 description, 최후 title+author+genre."""
    rich = (row.get("rich_description") or "").strip()
    if len(rich) >= _MIN_RICH:
        return rich[:2000], False
    desc = (row.get("description") or "").strip()
    if desc:
        return desc[:2000], True
    parts = [row.get("title") or "", row.get("author") or "", row.get("genre") or ""]
    return " ".join(p for p in parts if p).strip(), True


def _embed_text(text: str) -> list[float]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": [text], "dimensions": EMBEDDING_DIMENSIONS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -v`
Expected: PASS (3 text tests).

- [ ] **Step 5: Write failing test — `ensure_books_embedded` embed-once + per-book 격리**

```python
class _FakeTable:
    def __init__(self, store): self.store = store; self._sel=None; self._in=None
    def select(self, cols): self._sel=cols; return self
    def in_(self, col, ids): self._in=ids; return self
    def execute(self):
        if self._sel and self._in is not None and "desc_embedding" not in self._sel:
            return type("R", (), {"data": [{"id": i, **self.store["books"][i]} for i in self._in if i in self.store["books"]]})
        # existing book_v3_vectors ids
        return type("R", (), {"data": [{"book_id": b} for b in self.store["v3"]]})
    def upsert(self, row, on_conflict=None): self.store["v3"][row["book_id"]] = row; return self

class _FakeSB:
    def __init__(self, store): self.store=store
    def table(self, name):
        return _FakeTable(self.store)  # 간이: 테스트는 호출 수만 검증


def test_ensure_books_embedded_skips_already_present(monkeypatch):
    from engine import user_embed
    calls = []
    def fake_embed(t): calls.append(t); return [0.0]*2000
    store = {"books": {"B1": {"rich_description": "가"*250, "description": None, "title":"t","author":"a","genre":"g"}},
             "v3": {"B1": {"book_id": "B1"}}}  # B1 already embedded
    user_embed.ensure_books_embedded(["B1"], _FakeSB(store), embed_fn=fake_embed)
    assert calls == [], "이미 임베딩된 책은 OpenAI 호출 0회(embed-once)"
```

- [ ] **Step 6: Run to verify fail**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py::test_ensure_books_embedded_skips_already_present -v`
Expected: FAIL (`ensure_books_embedded` 없음).

- [ ] **Step 7: Implement `ensure_books_embedded` + `resolve_extra_query_vectors`**

```python
def ensure_books_embedded(book_ids, sb=None, embed_fn=None) -> None:
    """book_v3_vectors 에 없는 책을 가용 텍스트로 embed-once 저장. per-book best-effort."""
    if not book_ids:
        return
    sb = sb or get_supabase()
    embed_fn = embed_fn or _embed_text
    book_ids = list(dict.fromkeys(book_ids))
    existing = sb.table("book_v3_vectors").select("book_id").in_("book_id", book_ids).execute().data or []
    have = {r["book_id"] for r in existing}
    todo = [b for b in book_ids if b not in have]
    if not todo:
        return
    rows = sb.table("books").select(
        "id,title,author,genre,description,rich_description").in_("id", todo).execute().data or []
    for row in rows:
        try:
            text, provisional = _pick_source_text(row)
            if not text:
                continue
            emb = embed_fn(text)
            sb.table("book_v3_vectors").upsert({
                "book_id": row["id"], "desc_embedding": emb,
                "source_text": text[:2000], "provisional": provisional,
            }, on_conflict="book_id").execute()
        except Exception as e:  # per-book 격리 — 한 책 실패가 나머지를 막지 않음
            logger.warning("ensure_books_embedded failed b=%s: %s", row.get("id"), e)


def resolve_extra_query_vectors(liked_ids, bid_order_set, sb=None) -> dict:
    """인덱스(bid_order_set)에 없는 book_id 만 book_v3_vectors 에서 읽어 BookVectors 합성. OpenAI 없음."""
    sb = sb or get_supabase()
    missing = [b for b in liked_ids if b not in bid_order_set]
    if not missing:
        return {}
    rows = sb.table("book_v3_vectors").select(
        "book_id,desc_embedding").in_("book_id", missing).execute().data or []
    dim = EMBEDDING_DIMENSIONS
    out = {}
    for r in rows:
        if not r.get("desc_embedding"):
            continue
        out[r["book_id"]] = BookVectors(
            reasons=[], desc=to_np(r["desc_embedding"]),
            l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))
    return out
```

(주의: `genre_embeddings` 조회로 l1/l2를 채우는 건 후속 — MVP는 zero(`W_L1=W_L2=0`이라 무해). 위 `_FakeSB`는 간이라 Step 5 테스트가 통과하도록 `ensure_books_embedded`의 select/in_/execute 흐름만 만족하면 됨. 실패 시 FakeSB의 execute 분기를 실제 호출 순서에 맞게 보정.)

- [ ] **Step 8: Run to verify pass**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -v`
Expected: PASS (embed-once 호출 0회).

- [ ] **Step 9: Commit**

```bash
git add recommendation-server/engine/user_embed.py recommendation-server/tests/test_user_embed.py
git commit -m "feat: user_embed — 유저 책 embed-once + 인덱스 밖 책 벡터 resolve (C1/C2 헬퍼)"
```

---

## Task 4: C3 — 피드백 텍스트 조립 + 임베딩 (`user_embed.py`)

감정태그+한줄감상을 **무절단**으로 합쳐 임베딩. `experiment_confidence.py`의 `fmt_feedback`(이모지/40자 절단)은 차용 금지.

**Files:**
- Modify: `recommendation-server/engine/user_embed.py`
- Test: `recommendation-server/tests/test_user_embed.py`

**Interfaces:**
- Produces:
  - `build_feedback_text(emotion_tags: list[str] | None, review_text: str | None) -> str | None` — 태그/리뷰 둘 다 없으면 None. 형식: `"태그: a, b\n{review}"`, 리뷰 무절단.
  - `ensure_feedback_embedded(rows: list[dict], sb, embed_fn=None) -> None` — `feedback_embedding` 없고 태그/리뷰 있는 user_books 행을 임베딩·갱신. per-book best-effort.

- [ ] **Step 1: Write failing test — 무절단 + None 케이스**

```python
def test_build_feedback_text_full_review_no_truncation():
    from engine.user_embed import build_feedback_text
    review = "이 책은 " + "정말 "*50 + "좋았다"   # 200자+
    out = build_feedback_text(["문체", "분위기"], review)
    assert out.startswith("태그: 문체, 분위기\n")
    assert review in out  # 절단 금지
    assert "..." not in out and "리뷰:" not in out

def test_build_feedback_text_tags_only():
    from engine.user_embed import build_feedback_text
    assert build_feedback_text(["성장"], None) == "태그: 성장"

def test_build_feedback_text_none_when_empty():
    from engine.user_embed import build_feedback_text
    assert build_feedback_text(None, None) is None
    assert build_feedback_text([], "") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -k build_feedback_text -v`
Expected: FAIL.

- [ ] **Step 3: Implement `build_feedback_text` + `ensure_feedback_embedded`**

```python
def build_feedback_text(emotion_tags, review_text):
    tags = [t for t in (emotion_tags or []) if t and t.strip()]
    review = (review_text or "").strip()
    if not tags and not review:
        return None
    parts = []
    if tags:
        parts.append("태그: " + ", ".join(tags))
    if review:
        parts.append(review)  # 무절단 — 원문 보존(P4)
    return "\n".join(parts)


def ensure_feedback_embedded(rows, sb=None, embed_fn=None) -> None:
    """feedback_embedding 없고 태그/리뷰 있는 행을 임베딩·갱신. rows: user_books dict 리스트."""
    sb = sb or get_supabase()
    embed_fn = embed_fn or _embed_text
    for r in rows:
        if r.get("feedback_embedding"):
            continue
        text = build_feedback_text(r.get("emotion_tags"), r.get("review_text"))
        if not text:
            continue
        try:
            emb = embed_fn(text)
            sb.table("user_books").update({"feedback_embedding": emb}).eq(
                "user_id", r["user_id"]).eq("book_id", r["book_id"]).execute()
            r["feedback_embedding"] = emb  # in-place 갱신 → 호출측이 즉시 사용
        except Exception as e:
            logger.warning("ensure_feedback_embedded failed b=%s: %s", r.get("book_id"), e)
```

(주의: rows에 `user_id`가 필요 → recompute는 SELECT에 `user_id`를 포함하거나 호출 시 user_id를 각 행에 주입한다. Task 5에서 처리.)

- [ ] **Step 4: Run to verify pass**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -k build_feedback_text -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/user_embed.py recommendation-server/tests/test_user_embed.py
git commit -m "feat: 피드백 텍스트 조립(태그+리뷰 무절단) + ensure_feedback_embedded (C3)"
```

---

## Task 5: recompute 통합 (`cache.py`) — embed-first, post-embedding hash, augment, no-blank

**Files:**
- Modify: `recommendation-server/engine/cache.py`
- Test: `recommendation-server/tests/test_recompute_integration.py`

**Interfaces:**
- Consumes: `engine.user_embed.{ensure_feedback_embedded, ensure_books_embedded, resolve_extra_query_vectors}`, `app_state.bid_order`.
- Produces: `recompute_recommendations`가 인덱스 밖 좋아요 책·태그/리뷰를 같은 호출에서 임베딩·반영하고, computing 플래그가 기존 recs를 비우지 않는다.

- [ ] **Step 1: computing 플래그 no-blank (R2 NEW#1)**

`cache.py`의 computing 플래그 upsert(현재 `recommendations: []` 포함, 대략 cache.py:168-173)를 recommendations를 건드리지 않게 변경:

```python
    # computing 플래그 설정 — 기존 recommendations 는 보존(stale-serve 폴백 유지).
    try:
        existing_row = existing or {}
        sb.table("recommendation_cache").upsert(
            {"user_id": user_id, "computing": True, "input_hash": "__computing__",
             "recommendations": existing_row.get("recommendations", []),
             "computed_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="user_id",
        ).execute()
    except Exception as exc:
        logger.warning("recompute: failed to set computing flag for %s: %s", user_id, exc)
```

(신규 유저는 existing None → `[]`로 insert. 기존 유저는 직전 good recs 보존.)

- [ ] **Step 2: SELECT 확장 + embed-first + post-embedding 재read + augment**

`ub_res = sb.table("user_books").select("book_id,rating,feedback_embedding")...` 를 확장하고, **스코어링 전에** 임베딩하고 재read:

```python
    from engine.user_embed import (ensure_feedback_embedded, ensure_books_embedded,
                                    resolve_extra_query_vectors)
    # v3 폴백은 augment 안 함 — prod 는 prestacked(v4). 회귀 가시화.
    if app_state.prestacked_reasons is None:
        logger.warning("recompute: prestacked is None — v3 fallback (augment 미적용) u=%s", user_id)

    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding,emotion_tags,review_text"
    ).eq("user_id", user_id).execute()
    if not ub_res.data:
        ... (기존 빈 처리 유지)

    rated = [r for r in ub_res.data if r.get("rating") in ("good", "bad")]
    for r in rated:
        r["user_id"] = user_id  # ensure_feedback_embedded 가 키로 사용
    ensure_feedback_embedded(rated, sb)                                  # C3 (best-effort)
    ensure_books_embedded([r["book_id"] for r in rated], sb)            # C1 (best-effort)

    # post-embedding 재read → hash 가 live 와 일치(코히런스 수정 2)
    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding"
    ).eq("user_id", user_id).execute()
    input_hash = compute_input_hash(ub_res.data)
```

(기존 `input_hash = compute_input_hash(ub_res.data)` 한 줄은 위 재read 블록으로 대체. 이후 `liked_books`/`fb_data` 빌드는 기존 그대로.)

스코어링 직전 extra_query 빌드 + 전달:

```python
    bid_order_set = set(app_state.bid_order or [])
    extra_query = resolve_extra_query_vectors(list(liked_books.keys()), bid_order_set, sb)  # C2

    prestacked = app_state.prestacked_reasons
    if prestacked is not None:
        candidates = stage1_hybrid(
            liked_books, fb_data, app_state.desc_matrix_f16,
            app_state.agg_reason_matrix_f16, app_state.bid_order,
            top_n=STAGE1_TOP_N, extra_query=extra_query)
        scores = batch_score_prestacked(
            app_state.index, liked_books, fb_data, candidates, prestacked,
            extra_query=extra_query)
    else:
        scores = recommend_scores_two_stage(
            app_state.index, liked_books, fb_data, top_n=STAGE1_TOP_N)
```

- [ ] **Step 3: Write integration test — 인덱스 밖 책 + 피드백이 추천에 반영**

```python
# recommendation-server/tests/test_recompute_integration.py
import numpy as np
import types
from engine import cache as cache_mod
from engine.index import VectorIndex


def _unit(seed, dim=2000):
    rng = np.random.default_rng(seed); v = rng.standard_normal(dim).astype(np.float32); return v/np.linalg.norm(v)


def test_recompute_reflects_out_of_index_liked_book(monkeypatch):
    dim = 2000
    # 후보 인덱스: cand 만 존재(=USER_BOOK desc 와 유사)
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim), l1=np.zeros(dim), l2=np.zeros(dim))
    idx.build_desc_matrix()
    app_state = types.SimpleNamespace(
        index=idx, prestacked_reasons={}, bid_order=["cand"],
        desc_matrix_f16=np.stack([_unit(1, dim)]).astype(np.float16),
        agg_reason_matrix_f16=np.zeros((1, dim), np.float16),
        books_meta={"cand": {"title": "후보", "author": "저자", "cover_url": None}},
        built_at="2000-01-01",
    )
    # user_books: USER_BOOK(good, 인덱스 밖). resolve 가 USER_BOOK 벡터를 줌.
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: _StubSB())
    monkeypatch.setattr("engine.user_embed.ensure_feedback_embedded", lambda *a, **k: None)
    monkeypatch.setattr("engine.user_embed.ensure_books_embedded", lambda *a, **k: None)
    monkeypatch.setattr("engine.user_embed.resolve_extra_query_vectors",
                        lambda ids, s, sb=None: {"USER_BOOK": __import__("engine.index", fromlist=["BookVectors"]).BookVectors(
                            reasons=[], desc=_unit(1, dim), l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))})
    saved = {}
    monkeypatch.setattr(cache_mod, "save_cache_if_current",
                        lambda uid, recs, *a, **k: saved.update(recs=recs))
    cache_mod.recompute_recommendations("U1", app_state)
    assert saved.get("recs"), "인덱스 밖 좋아요 책 기반 추천이 생성돼야 함"
    assert saved["recs"][0]["book_id"] == "cand"
```

`_StubSB`는 user_books SELECT가 `[{"book_id":"USER_BOOK","rating":"good","feedback_embedding":None}]`(+확장 SELECT엔 emotion_tags/review_text None)을 반환하고, recommendation_cache upsert/load는 no-op이 되게 최소 구현. (load_cache는 None 반환하도록 monkeypatch 가능.)

- [ ] **Step 4: Run integration test**

Run: `cd recommendation-server && python -m pytest tests/test_recompute_integration.py -v`
Expected: PASS (stub 보정이 필요할 수 있음 — SELECT 분기/Plugin 흐름 맞출 것).

- [ ] **Step 5: Full server suite green**

Run: `cd recommendation-server && python -m pytest tests/ -q`
Expected: 전부 PASS(기존 + 신규). 실패 시 시그니처/import 보정.

- [ ] **Step 6: Commit**

```bash
git add recommendation-server/engine/cache.py recommendation-server/tests/test_recompute_integration.py
git commit -m "feat: recompute 통합 — embed-first + post-embedding hash + extra_query + computing no-blank"
```

---

## Task 6: inline 경로 augment (`recommend_core.py`)

inline은 OpenAI 없이 **이미 임베딩된** 인덱스 밖 책을 즉시 반영(resolve only).

**Files:**
- Modify: `recommendation-server/engine/recommend_core.py`
- Test: `recommendation-server/tests/test_twostage_augment.py` (compute_scored_books 케이스 추가)

**Interfaces:**
- Produces: `compute_scored_books(..., extra_query=None)`, `try_compute_inline(app_state, liked_books, fb_data, extra_query=None)`.

- [ ] **Step 1: Write failing test**

```python
def test_compute_scored_books_threads_extra_query():
    import numpy as np
    from engine.index import VectorIndex, BookVectors
    from engine.recommend_core import compute_scored_books
    dim = 2000
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim), l1=np.zeros(dim), l2=np.zeros(dim))
    extra = {"UB": BookVectors(reasons=[], desc=_unit(1, dim), l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))}
    out = compute_scored_books(
        index=idx, liked_books={"UB": {"rating": "good"}}, fb_data={},
        prestacked_reasons={}, desc_matrix_f16=np.stack([_unit(1, dim)]).astype(np.float16),
        agg_reason_matrix_f16=np.zeros((1, dim), np.float16), bid_order=["cand"], extra_query=extra)
    assert out and out[0][0] == "cand"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py::test_compute_scored_books_threads_extra_query -v`
Expected: FAIL (unexpected kwarg).

- [ ] **Step 3: Implement**

`compute_scored_books`에 `extra_query=None` 추가 → `stage1_hybrid(..., extra_query=extra_query)`, `batch_score_prestacked(..., extra_query=extra_query)`. `try_compute_inline`에 `extra_query=None` 추가 → `compute_scored_books(..., extra_query=extra_query)`로 전달.

- [ ] **Step 4: Run to verify pass + suite**

Run: `cd recommendation-server && python -m pytest tests/test_twostage_augment.py -v && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/recommend_core.py recommendation-server/tests/test_twostage_augment.py
git commit -m "feat: inline 경로 extra_query 통과 (이미 임베딩된 인덱스 밖 책 즉시 반영)"
```

---

## Task 7: C4 트리거 (`recommend.py` + `home.py`)

SELECT 확장 + 술어 + inline에 extra_query 전달 + 술어 참이면 recompute 무조건 큐잉 & 빈약 캐시 저장 skip.

**Files:**
- Modify: `recommendation-server/api/recommend.py`
- Modify: `recommendation-server/api/home.py`

**Interfaces:**
- Consumes: `engine.user_embed.resolve_extra_query_vectors`, `app_state.bid_order`.

- [ ] **Step 1: recommend.py — SELECT 확장**

`recommend.py:64-66`의 `.select("book_id,rating,feedback_embedding")` → `.select("book_id,rating,feedback_embedding,emotion_tags,review_text")`.

- [ ] **Step 2: recommend.py — 술어 + inline extra_query + queue/skip**

캐시미스 블록(현재 `liked_books`/`fb_data` 빌드 직후)에 추가:

```python
    bid_set = set(request.app.state.bid_order or [])
    needs_bg = any(
        (ub.get("rating") in ("good", "bad") and ub["book_id"] not in bid_set)
        or ((ub.get("emotion_tags") or ub.get("review_text")) and not ub.get("feedback_embedding"))
        for ub in ub_res.data
    )
    extra_query = resolve_extra_query_vectors(list(liked_books.keys()), bid_set, sb) if bid_set else {}

    scored = await try_compute_inline(request.app.state, liked_books, fb_data, extra_query=extra_query)
    if scored is not None:
        ... (기존 recs/recs_for_cache 빌드)
        if not needs_bg:                                   # 완전 임베딩 상태에서만 캐시 확정
            background_tasks.add_task(save_cache_if_current, user_id, recs_for_cache,
                                      input_hash, total_liked, total_disliked, has_feedback)
        if needs_bg:                                       # 미임베딩 → 항상 백그라운드 recompute
            background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
        ... (기존 dedup + return)
```

no-slot 분기(현재 `background_tasks.add_task(recompute_recommendations, ...)`)는 그대로 유지.

- [ ] **Step 3: home.py — 동일 적용**

home.py의 user_books SELECT(변수 `user_books` 채우는 `.select(...)`)에 `emotion_tags,review_text` 추가. tier2 inline 분기(home.py:333 `recommend_scored = await try_compute_inline(...)`)를 `extra_query` 포함으로 바꾸고, 술어 `needs_bg`가 참이면 inline 성공 시에도 `recompute_recommendations` 큐잉 + `recs_pending` 무관하게 빈약본은 캐시 skip(이미 `if not recs_pending` 게이트 존재 — `needs_bg`도 함께 고려):

```python
    bid_set = set(request.app.state.bid_order or [])
    needs_bg = any(
        (ub.get("rating") in ("good","bad") and ub["book_id"] not in bid_set)
        or ((ub.get("emotion_tags") or ub.get("review_text")) and not ub.get("feedback_embedding"))
        for ub in user_books)
    extra_query = resolve_extra_query_vectors([ub["book_id"] for ub in user_books], bid_set, sb) if bid_set else {}
    recommend_scored = await try_compute_inline(request.app.state, liked_books, fb_data, extra_query=extra_query)
    if recommend_scored is None:
        background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
        recommend_scored = []; recs_pending = True
    elif needs_bg:
        background_tasks.add_task(recompute_recommendations, user_id, request.app.state)
        recs_pending = True   # 빈약본 캐시 방지(home_cache skip) — 다음 로드에 보강본
```

(home.py의 `sb` 핸들은 함수 내 기존 변수 사용; 없으면 `sb = get_supabase()`.)

- [ ] **Step 4: Server suite green**

Run: `cd recommendation-server && python -m pytest tests/ -q`
Expected: PASS. (기존 recommend/home 테스트가 시그니처 변경 반영하도록 보정 필요할 수 있음.)

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/api/recommend.py recommendation-server/api/home.py
git commit -m "feat: C4 트리거 — SELECT 확장+술어+extra_query inline+미임베딩 시 recompute 무조건 큐잉/빈약캐시 skip"
```

---

## Task 8: 배치 동기화 — `backfill_feedback_embedding.py` 태그 포함

**Files:**
- Modify: `scripts/backfill_feedback_embedding.py`

- [ ] **Step 1: 임베딩 입력을 build_feedback_text로 교체**

스크립트가 `review_text`만 임베딩하던 부분을 태그+리뷰로 교체. user_books SELECT에 `emotion_tags`가 없으면 추가하고, 임베딩 입력 텍스트를:

```python
# (스크립트 로컬 헬퍼 — engine import 불가 시 동일 로직 복제)
def _fb_text(tags, review):
    tags = [t for t in (tags or []) if t and str(t).strip()]
    review = (review or "").strip()
    if not tags and not review:
        return None
    parts = []
    if tags: parts.append("태그: " + ", ".join(tags))
    if review: parts.append(review)
    return "\n".join(parts)
```
로 만들고, `if text:` 일 때만 임베딩. (태그만 있어도 임베딩되도록 — 기존엔 review 없으면 skip.)

- [ ] **Step 2: 사전 dry-run(쓰기 없이 텍스트 조립만) 확인**

Run: `cd scripts && python -c "import backfill_feedback_embedding as b; print(b._fb_text(['문체'], None)); print(b._fb_text(None,'좋음')); print(b._fb_text(None,None))"`
Expected: `태그: 문체` / `좋음` / `None`

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_feedback_embedding.py
git commit -m "feat: backfill 피드백 임베딩에 감정태그 포함 (라이브 경로와 동기화)"
```

---

## Task 9: 검증 게이트 — 로컬 + prod E2E

**로컬(머지 전, 필수):**
- [ ] `cd recommendation-server && python -m pytest tests/ -q` 전부 green.
- [ ] `cd scripts && python -m pytest tests/ -q` 전부 green(backfill 변경 회귀).
- [ ] 핵심 시나리오 단위로 재확인: 인덱스 밖 good 책만 → 추천 非빈 / 태그만 남긴 유저 → 태그 반영 / centroid 아님(per-book) / computing 중 기존 recs 보존.

**prod E2E (머지·배포 후, Eden 명시 승인 필요 — [[ref_prod_e2e_throwaway]]):**
- [ ] throwaway 유저로 **인덱스에 없는 책 6+권** 좋아요(일부에 감정태그/리뷰) → 마이그레이션 apply 확인(`book_v3_vectors.provisional`) → `/recommend` 1차(미스, best-effort) → 백그라운드 1사이클 후 2차 호출이 그 책 기반 추천 반환·非빈 확인.
- [ ] **태그 A/B**: 같은 책 집합, 태그 유무로 추천 순위가 달라지는지.
- [ ] **얕은 vs rich**: title-only 임베딩 책이 추천 품질을 저하시키지 않는지 정성 비교.
- [ ] `/health` memory_mb OOM 0(382/512 헤드룸 유지).
- [ ] throwaway 유저 정리(auth sweep 0).

**롤백:** feature 브랜치 미머지면 prod 영향 0. 머지 후 문제 시 revert 커밋 → 재배포(CODE_REV bump).

---

## Self-Review (작성자 체크 결과)

- **스펙 커버리지:** C1=Task3, C2=Task2+6, C3=Task4+(5에서 호출), C4=Task7, 코히런스=Task5(computing no-blank + post-embedding hash + inline skip), 마이그레이션=Task1, backfill=Task8, 검증=Task9. 후보풀/surfacing/Phase2는 스펙에서 OUT — 태스크 없음(정상).
- **플레이스홀더:** 없음(테스트/구현 코드 실내용 포함). FakeSB/Stub은 "흐름 맞춰 보정" 명시 — 구현 시 실제 호출 순서로 채울 것.
- **타입 일관성:** `extra_query: dict[str, BookVectors]`가 twostage/recommend_core/cache 전반 동일. `resolve_extra_query_vectors`/`ensure_books_embedded`/`ensure_feedback_embedded`/`build_feedback_text` 시그니처 Task3/4 정의 ↔ Task5/6/7 사용 일치. `_pick_source_text` 반환 `(text, provisional)` 일관.
- **알려진 보정 포인트:** Task3/5의 Fake/Stub SB는 간이 — 실제 supabase-py 체이닝(`.select().in_().execute()`, `.update().eq().eq().execute()`)에 맞춰 테스트 더블을 보정해야 green. 구현 시 실제 호출 순서 우선.
