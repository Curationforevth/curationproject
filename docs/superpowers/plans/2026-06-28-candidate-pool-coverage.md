# 후보풀 커버리지 (DB의 모든 책을 가진 정보 최대로 추천) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배치 임베딩이 `rich_description≥200자` 책만 후보풀에 넣던 게이트를 제거하고, DB의 모든 책을 가진 정보 최대로(rich→카카오설명→title+author+genre) 임베딩해 추천/유사 후보풀에 편입하되, 빈약한 책은 `source_tier`로 경미하게 down-weight한다.

**Architecture:** 배치(`scripts/`)와 라이브(`recommendation-server/`)가 동일한 단계적 폴백 정책을 쓰고(배포 경계로 코드 공유 불가 → 동등성 테스트로 동기화), `source_tier`(rich/kakao_desc/minimal)를 `book_v3_vectors`에 기록한다. 인덱스 빌드가 tier를 `VectorIndex._candidate_tier`로 운반(pkl 직렬화)해 loader/app_state arity 변경 없이 스코어러가 후보를 차등 감점한다(stage2 + /similar, positive-part 곱셈으로 부호 안전).

**Tech Stack:** Python 3.11, Supabase(PostgreSQL+pgvector), OpenAI text-embedding-3-large(2000D), numpy float16/32, pytest, GitHub Actions, Render.

**근거 스펙:** `docs/superpowers/specs/2026-06-28-candidate-pool-coverage-design.md` (v3, 2라운드 적대적 리뷰 반영).

## Global Constraints

- 임베딩 모델/차원: `text-embedding-3-large`, `EMBEDDING_DIMENSIONS=2000` (config.py / scripts/lib/openai_helpers — 양쪽 동일).
- tier 문자열 정본: `"rich"`, `"kakao_desc"`, `"minimal"` (4곳에서 정확히 일치해야 함: scripts `build_desc_source`, 라이브 `_pick_source_text`, config `SOURCE_TIER_PENALTY`, 마이그레이션 CHECK).
- `_MIN_RICH = 200` (rich 판정 길이 임계, 양쪽 동일).
- 폴백 정책은 런타임 공유 불가(배포 경계) → **동등성/단일성 테스트가 유일한 동기화 장치**.
- OpenAI 호출은 **배치(GitHub Actions)·백그라운드 recompute**에서만. 요청/인라인 경로 0.
- DB 쓰기 경로는 dry-run으로 검증 불가(CLAUDE.md) → mode=small 실쓰기 / throwaway prod E2E.
- **비용 게이트:** 대량 embed-once·인덱스 재빌드는 Eden 승인 후(§Rollout). 코드+테스트+마이그레이션은 비용 0.
- 앱 변경 0.

---

### Task 1: 마이그레이션 — `source_tier` 컬럼 + 기존 thin 행 backfill

**Files:**
- Create: `supabase/migrations/20260628000001_book_v3_vectors_source_tier.sql`

**Interfaces:**
- Produces: `book_v3_vectors.source_tier TEXT NOT NULL` (값 ∈ {rich,kakao_desc,minimal}). 기존 `provisional=TRUE` 행 → `'kakao_desc'`(임시, Task 10 reembed가 교정). `provisional=FALSE` → `'rich'`(DEFAULT).

> DDL은 단위테스트 불가(CLAUDE.md) — apply-migrations 자동 적용 + 다운스트림(build/reembed) 읽기로 검증. 백필이 핵심: C1 `ensure_books_embedded`가 이미 prod 라이브라 `provisional=TRUE` thin 행이 존재 → 블랭킷 DEFAULT 'rich'면 오라벨된다(리뷰 R2 BLOCKER).

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- book_v3_vectors.source_tier: 임베딩 소스 품질 등급 (rich / kakao_desc / minimal)
-- rich       = rich_description >= 200자 (clean_html 후)
-- kakao_desc = 카카오 description
-- minimal    = title+author+genre (최후 폴백)
ALTER TABLE book_v3_vectors
  ADD COLUMN IF NOT EXISTS source_tier TEXT NOT NULL DEFAULT 'rich';

ALTER TABLE book_v3_vectors
  DROP CONSTRAINT IF EXISTS book_v3_vectors_source_tier_check;
ALTER TABLE book_v3_vectors
  ADD CONSTRAINT book_v3_vectors_source_tier_check
  CHECK (source_tier IN ('rich', 'kakao_desc', 'minimal'));

-- 기존 provisional thin 행 backfill(블랭킷 'rich' 오라벨 방지). provisional 비트만으론
-- kakao_desc/minimal 구분 불가 → 'kakao_desc'로 임시 라벨, reembed(Task 10)가 정확 교정.
UPDATE book_v3_vectors SET source_tier = 'kakao_desc' WHERE provisional = TRUE;
```

- [ ] **Step 2: SQL 문법 로컬 점검 (psql dry parse 불가 시 육안 + CHECK 값 확인)**

Run: `grep -nE "source_tier|CHECK|provisional" supabase/migrations/20260628000001_book_v3_vectors_source_tier.sql`
Expected: 컬럼 추가 / CHECK 3값 / backfill UPDATE 3줄 확인.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260628000001_book_v3_vectors_source_tier.sql
git commit -m "feat: book_v3_vectors.source_tier 컬럼 + 기존 provisional 행 backfill"
```

---

### Task 2: config 상수 — 페널티 + /similar 최소 tier

**Files:**
- Modify: `recommendation-server/config.py`

**Interfaces:**
- Produces: `SOURCE_TIER_PENALTY: dict[str,float]`, `SIMILAR_MIN_TIER: str`. Task 7·8이 소비.

- [ ] **Step 1: 상수 추가**

```python
# 후보 품질 등급별 down-weight (타이브레이크 수준 — niche 역전 방지, E2E로 튜닝).
# positive-part 곱셈으로만 적용(음수 점수 미변경 — 부호 안전).
SOURCE_TIER_PENALTY = {"rich": 1.0, "kakao_desc": 0.95, "minimal": 0.85}
# /similar(항상 보이는 정밀 surface)에서는 minimal tier 노출 제외. /recommend는 유지.
SIMILAR_MIN_TIER = "kakao_desc"
```

- [ ] **Step 2: import 가능 확인**

Run: `cd recommendation-server && python -c "from config import SOURCE_TIER_PENALTY, SIMILAR_MIN_TIER; print(SOURCE_TIER_PENALTY, SIMILAR_MIN_TIER)"`
Expected: `{'rich': 1.0, 'kakao_desc': 0.95, 'minimal': 0.85} kakao_desc`

- [ ] **Step 3: Commit**

```bash
git add recommendation-server/config.py
git commit -m "feat: SOURCE_TIER_PENALTY + SIMILAR_MIN_TIER 상수"
```

---

### Task 3: `clean_html` — recommendation-server 경량 유틸 (M3 동등성 토대)

**Files:**
- Modify: `recommendation-server/engine/utils.py`
- Test: `recommendation-server/tests/test_utils.py` (없으면 생성)

**Interfaces:**
- Produces: `clean_html(text: str) -> str` — `scripts/lib/genre_parser.clean_html`과 동작 동일(`re.sub(r"<[^>]+>", "", text)`). 평문에 idempotent. Task 4가 소비.

> scripts/lib는 Render에 안 실림(배포 경계) → import 금지, 별도 구현(동작은 동일해야 함).

- [ ] **Step 1: 실패 테스트 작성**

```python
# recommendation-server/tests/test_utils.py
from engine.utils import clean_html

def test_clean_html_strips_tags():
    assert clean_html("<p>안녕<br>세상</p>") == "안녕세상"

def test_clean_html_idempotent_on_plaintext():
    s = "태그 없는 평문"
    assert clean_html(s) == s

def test_clean_html_none_safe():
    assert clean_html(None) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_utils.py -v`
Expected: FAIL (ImportError clean_html 또는 미정의)

- [ ] **Step 3: 구현**

```python
# recommendation-server/engine/utils.py 에 추가
import re

def clean_html(text):
    """HTML 태그 제거. scripts/lib/genre_parser.clean_html 과 동작 동일(배포 경계로 코드 미공유)."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)
```

- [ ] **Step 4: 통과 확인**

Run: `cd recommendation-server && python -m pytest tests/test_utils.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/utils.py recommendation-server/tests/test_utils.py
git commit -m "feat: engine.clean_html (M3 폴백 동등성 토대)"
```

---

### Task 4: 라이브 `_pick_source_text` → `(text, tier)` + clean_html, `ensure_books_embedded` source_tier 기록

**Files:**
- Modify: `recommendation-server/engine/user_embed.py:26-38` (`_pick_source_text`), `:82-87` (upsert)
- Test: `recommendation-server/tests/test_user_embed.py`

**Interfaces:**
- Consumes: `clean_html` (Task 3).
- Produces: `_pick_source_text(row: dict) -> tuple[str|None, str|None]` 반환을 `(text, source_tier)` 로 변경(tier ∈ rich/kakao_desc/minimal). `ensure_books_embedded` upsert에 `source_tier` + `provisional`(=tier!='rich') 기록.

- [ ] **Step 1: 실패 테스트 작성**

```python
# recommendation-server/tests/test_user_embed.py 에 추가
from engine.user_embed import _pick_source_text

def test_pick_source_text_rich():
    row = {"rich_description": "가"*250}
    text, tier = _pick_source_text(row)
    assert tier == "rich" and len(text) == 250

def test_pick_source_text_rich_html_stripped_below_gate():
    # 태그 포함 250자지만 정제 후 <200 → kakao_desc 로 폴백(배치와 동일 게이트)
    row = {"rich_description": "<p>" + "가"*180 + "</p>", "description": "카카오 설명"}
    text, tier = _pick_source_text(row)
    assert tier == "kakao_desc" and text == "카카오 설명"

def test_pick_source_text_kakao_desc():
    row = {"rich_description": "", "description": "짧은 카카오 줄거리"}
    text, tier = _pick_source_text(row)
    assert tier == "kakao_desc" and text == "짧은 카카오 줄거리"

def test_pick_source_text_minimal():
    row = {"title": "제목", "author": "저자", "genre": "소설"}
    text, tier = _pick_source_text(row)
    assert tier == "minimal" and text == "제목 저자 소설"

def test_pick_source_text_empty():
    text, tier = _pick_source_text({})
    assert text is None and tier is None
```

- [ ] **Step 2: 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -k pick_source_text -v`
Expected: FAIL (현재 `(text, provisional bool)` 반환 → tier 문자열 assert 실패)

- [ ] **Step 3: 구현**

```python
# engine/user_embed.py
from engine.utils import to_np, clean_html  # clean_html 추가

def _pick_source_text(row: dict) -> tuple:
    """(임베딩 텍스트, source_tier). rich≥200(clean_html 후) → 카카오 description → title+author+genre.

    배치 build_desc_source 와 정책 동일(배포 경계로 코드 미공유, 동등성 테스트로 동기화).
    """
    rich = clean_html(row.get("rich_description") or "").strip()
    if len(rich) >= _MIN_RICH:
        return rich[:2000], "rich"
    desc = clean_html(row.get("description") or "").strip()
    if desc:
        return desc[:2000], "kakao_desc"
    parts = [row.get("title") or "", row.get("author") or "", row.get("genre") or ""]
    m = " ".join(p for p in parts if p).strip()
    return (m[:2000], "minimal") if m else (None, None)
```

```python
# engine/user_embed.py ensure_books_embedded 내부 upsert (현재 :77-87)
            text, tier = _pick_source_text(row)
            if not text:
                continue
            emb = embed_fn(text)
            sb.table("book_v3_vectors").upsert({
                "book_id": row["id"],
                "desc_embedding": emb,
                "source_text": text[:2000],
                "source_tier": tier,
                "provisional": tier != "rich",
            }, on_conflict="book_id").execute()
```

- [ ] **Step 4: 통과 확인 + 기존 user_embed 테스트 회귀**

Run: `cd recommendation-server && python -m pytest tests/test_user_embed.py -v`
Expected: PASS (신규 5 + 기존 통과. 기존 테스트가 `(text, provisional)` 언패킹하면 같이 수정.)

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/user_embed.py recommendation-server/tests/test_user_embed.py
git commit -m "feat: _pick_source_text (text,tier) 반환 + clean_html + source_tier 기록"
```

---

### Task 5: 공유 동등성 픽스처 + 배치 `build_desc_source` 단계적 폴백 + tier 단일성 테스트

**Files:**
- Create: `tests/fixtures/source_tier_cases.json`
- Modify: `scripts/generate_book_v3_vectors.py:35-46` (`build_desc_source`)
- Test: `tests/test_generate_book_v3_vectors.py` (생성), `recommendation-server/tests/test_user_embed.py` (동등성 추가)

**Interfaces:**
- Consumes: `scripts/lib/genre_parser.clean_html` (기존).
- Produces: `build_desc_source(book: dict) -> tuple[str|None, str|None]` = `(text, source_tier)`, `_pick_source_text`와 동일 출력.

- [ ] **Step 1: 공유 픽스처 작성**

```json
[
  {"row": {"rich_description": "RICHRICHRICH... (250자)"}, "text_len": 250, "tier": "rich"},
  {"row": {"rich_description": "<p>(180자)</p>", "description": "카카오 설명"}, "text": "카카오 설명", "tier": "kakao_desc"},
  {"row": {"description": "짧은 카카오 줄거리"}, "text": "짧은 카카오 줄거리", "tier": "kakao_desc"},
  {"row": {"title": "제목", "author": "저자", "genre": "소설"}, "text": "제목 저자 소설", "tier": "minimal"},
  {"row": {}, "text": null, "tier": null}
]
```
(실제 250/180자는 `"가"*250` 등으로 생성 — JSON엔 명시 문자열로 저장하거나 테스트에서 길이 합성. 두 테스트가 같은 파일을 읽어 동일 기대값 사용.)

- [ ] **Step 2: 실패 테스트 작성 (배치 + 동등성)**

```python
# tests/test_generate_book_v3_vectors.py
import json, os
from scripts.generate_book_v3_vectors import build_desc_source

CASES = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures/source_tier_cases.json")))

def test_build_desc_source_matches_fixture():
    for c in CASES:
        text, tier = build_desc_source(c["row"])
        assert tier == c["tier"]
        if c.get("text") is not None:
            assert text == c["text"]

def test_tier_strings_single_source():
    from config import SOURCE_TIER_PENALTY  # recommendation-server config (sys.path 조정 필요 시 스킵)
    assert set(SOURCE_TIER_PENALTY) == {"rich", "kakao_desc", "minimal"}
```

```python
# recommendation-server/tests/test_user_embed.py 에 동등성 추가 (../tests/fixtures 읽기)
import json, os
def test_pick_source_text_equivalence_with_batch_fixture():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "source_tier_cases.json")
    for c in json.load(open(path)):
        text, tier = _pick_source_text(c["row"])
        assert tier == c["tier"]
        if c.get("text") is not None:
            assert text == c["text"]
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_generate_book_v3_vectors.py -v`
Expected: FAIL (build_desc_source가 현재 rich-only `None` 반환 / 튜플 아님)

- [ ] **Step 4: 구현**

```python
# scripts/generate_book_v3_vectors.py
from scripts.lib.genre_parser import parse_genre, clean_html  # 이미 import됨

_MIN_RICH = 200

def build_desc_source(book):
    """desc 임베딩 소스 + 품질 등급. 라이브 _pick_source_text 와 동일 정책(동등성 테스트로 동기화).

    rich≥200(clean_html 후) → 카카오 description → title+author+genre. 반환 (text|None, tier|None).
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
```

- [ ] **Step 5: 통과 확인 (양쪽)**

Run: `python -m pytest tests/test_generate_book_v3_vectors.py -v && cd recommendation-server && python -m pytest tests/test_user_embed.py -k equivalence -v`
Expected: PASS (배치·라이브가 같은 픽스처에 동일 출력)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/source_tier_cases.json tests/test_generate_book_v3_vectors.py scripts/generate_book_v3_vectors.py recommendation-server/tests/test_user_embed.py
git commit -m "feat: build_desc_source 단계적 폴백 + 배치/라이브 동등성 픽스처"
```

---

### Task 6: `fetch_target_books` 전체 대상화 + main() source_tier 기록

**Files:**
- Modify: `scripts/generate_book_v3_vectors.py:84-99` (`fetch_target_books`), `:147-170` (prepared 루프), `:228-240` (upsert rows)
- Test: `tests/test_generate_book_v3_vectors.py`

**Interfaces:**
- Consumes: `build_desc_source` (Task 5, `(text,tier)`).
- Produces: `fetch_target_books`가 `rich_description` 무관 전체 books(미임베딩분) 조회, `author` 포함. upsert row에 `source_tier`, `provisional`.

- [ ] **Step 1: 실패 테스트 작성 (가짜 sb)**

```python
# tests/test_generate_book_v3_vectors.py 에 추가
def test_fetch_target_books_no_rich_filter_includes_author(monkeypatch):
    from scripts import generate_book_v3_vectors as g
    captured = {}
    class FakeQ:
        def select(self, cols): captured["cols"] = cols; return self
        def not_(self): captured["not_called"] = True; return self
        def is_(self, *a): return self
        def range(self, a, b): return self
        def execute(self):
            class R: data = []
            return R()
    class FakeSB:
        def table(self, t): return FakeQ()
    g.fetch_target_books(FakeSB(), 10)
    assert "author" in captured["cols"]
    assert "not_called" not in captured  # rich 필터 제거됨
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_generate_book_v3_vectors.py -k fetch_target -v`
Expected: FAIL (현재 `.not_.is_("rich_description","null")` 존재 + author 없음)

- [ ] **Step 3: 구현 (fetch + prepared + upsert)**

```python
# fetch_target_books — rich 필터 제거, author 추가, 기존 임베딩분 제외 후 limit
def fetch_target_books(sb, limit, existing_ids=None):
    existing_ids = existing_ids or set()
    books, offset = [], 0
    while len(books) < limit:
        res = with_retry(lambda o=offset: sb.table("books")
                         .select("id, title, author, genre, description, rich_description")
                         .range(o, o + 999).execute())
        if not res.data:
            break
        books.extend(b for b in res.data if b["id"] not in existing_ids)
        if len(res.data) < 1000:
            break
        offset += 1000
    return books[:limit]
```

```python
# main(): existing 먼저 계산해 fetch에 전달 (대표성), prepared 에 tier 보존
    existing = get_existing_book_ids(sb) | load_checkpoint()
    all_books = fetch_target_books(sb, args.limit, existing_ids=existing)
    books = all_books  # 이미 existing 제외됨
    ...
    for book in books:
        source, tier = build_desc_source(book)
        if source is None:
            skipped_shallow += 1   # 텍스트 전무(제목조차 없음)만 skip
            continue
        l1, l2 = parse_genre(book.get("genre"))
        ...
        prepared.append({..., "source_text": source, "source_tier": tier, ...})
```

```python
# 배치 upsert rows 에 source_tier/provisional 추가
            rows.append({
                "book_id": p["book_id"],
                "desc_embedding": emb,
                "source_text": p["source_text"],
                "source_tier": p["source_tier"],
                "provisional": p["source_tier"] != "rich",
                "l1_text": p["l1_text"], "l2_text": p["l2_text"],
                "l1_genre_id": p["l1_genre_id"], "l2_genre_id": p["l2_genre_id"],
            })
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_generate_book_v3_vectors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_book_v3_vectors.py tests/test_generate_book_v3_vectors.py
git commit -m "feat: 배치 임베딩 전체 books 대상화 + source_tier 기록 (rich 게이트 제거)"
```

---

### Task 7: VectorIndex — tier 운반 + similar 차등 down-weight & minimal 제외

**Files:**
- Modify: `recommendation-server/engine/index.py:19-27` (`__init__`), `:51-57` (`build_desc_matrix`), `:59-77` (`similar_by_vector`)
- Test: `recommendation-server/tests/test_index.py`, `recommendation-server/tests/test_similar_by_vector.py`

**Interfaces:**
- Consumes: `SOURCE_TIER_PENALTY`, `SIMILAR_MIN_TIER` (config).
- Produces: `VectorIndex._candidate_tier: dict[str,str]` (non-rich만, sparse), `_penalty_vec`, `_exclude_similar`. `similar_by_vector`가 positive-part 페널티 + minimal 제외 적용. 구 pkl은 `getattr` 폴백.

- [ ] **Step 1: 실패 테스트 작성**

```python
# recommendation-server/tests/test_index.py 에 추가
import numpy as np
from engine.index import VectorIndex

def _mk(dim=4):
    idx = VectorIndex(dim=dim, dtype=np.float32)
    for bid, v in [("rich", [1,0,0,0]), ("kdesc", [0.9,0.1,0,0]), ("min", [0.95,0,0,0])]:
        idx.add_book(bid, reasons=[], desc=np.array(v,dtype=np.float32),
                     l1=np.zeros(dim,np.float32), l2=np.zeros(dim,np.float32))
    idx._candidate_tier = {"kdesc": "kakao_desc", "min": "minimal"}
    idx.build_desc_matrix()
    return idx

def test_similar_penalizes_and_excludes_minimal():
    idx = _mk()
    res = dict(idx.similar_by_vector(np.array([1,0,0,0],dtype=np.float32), limit=10))
    assert "min" not in res                       # minimal 제외 (SIMILAR_MIN_TIER)
    assert res["kdesc"] < 0.9                      # kakao_desc 감점(0.95배)
    assert abs(res["rich"] - 1.0) < 1e-5           # rich 무감점

def test_old_pkl_without_candidate_tier_safe():
    idx = VectorIndex(dim=4, dtype=np.float32)
    idx.add_book("a", reasons=[], desc=np.array([1,0,0,0],dtype=np.float32),
                 l1=np.zeros(4,np.float32), l2=np.zeros(4,np.float32))
    if hasattr(idx, "_candidate_tier"): del idx._candidate_tier  # 구 pkl 시뮬
    idx.build_desc_matrix()
    res = dict(idx.similar_by_vector(np.array([1,0,0,0],dtype=np.float32), limit=10))
    assert abs(res["a"] - 1.0) < 1e-5  # 폴백 무감점
```

- [ ] **Step 2: 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_index.py -k "penaliz or old_pkl" -v`
Expected: FAIL (페널티/제외 미구현)

- [ ] **Step 3: 구현**

```python
# engine/index.py
from config import SOURCE_TIER_PENALTY, SIMILAR_MIN_TIER

# __init__ 에 추가
        self._candidate_tier: dict[str, str] = {}
        self._penalty_vec = None
        self._exclude_similar: set[str] = set()

# build_desc_matrix 끝에 추가 (구 pkl 안전 위해 getattr)
    def build_desc_matrix(self):
        self._desc_bid_order = list(self._books.keys())
        descs = [self._books[bid].desc for bid in self._desc_bid_order]
        self._desc_matrix = np.stack(descs)
        self._desc_bid_to_idx = {bid: i for i, bid in enumerate(self._desc_bid_order)}
        tier = getattr(self, "_candidate_tier", {})
        self._penalty_vec = np.array(
            [SOURCE_TIER_PENALTY[tier.get(bid, "rich")] for bid in self._desc_bid_order],
            dtype=np.float32)
        self._exclude_similar = {b for b, t in tier.items() if t == "minimal"}

# similar_by_vector: penalty(positive-part) + minimal 제외
    def similar_by_vector(self, query_vec, exclude_ids=None, limit=10):
        if self._desc_matrix is None:
            self.build_desc_matrix()
        exclude_ids = set(exclude_ids or set()) | getattr(self, "_exclude_similar", set())
        scores = self._desc_matrix @ query_vec.astype(self.dtype)
        pv = getattr(self, "_penalty_vec", None)
        if pv is not None:
            scores = np.where(scores > 0, scores * pv, scores)
        for ex in exclude_ids:
            i = self._desc_bid_to_idx.get(ex)
            if i is not None:
                scores[i] = -999.0
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i])) for i in top_idx if scores[i] > -900.0]
```

- [ ] **Step 4: 통과 확인 + 기존 similar 회귀**

Run: `cd recommendation-server && python -m pytest tests/test_index.py tests/test_similar_by_vector.py tests/test_similar_union.py -v`
Expected: PASS (신규 + 기존. 기존 테스트는 _candidate_tier 미설정=전부 rich라 무감점 → 동치)

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/index.py recommendation-server/tests/test_index.py
git commit -m "feat: VectorIndex tier 운반 + similar 차등 down-weight & minimal 제외"
```

---

### Task 8: stage2 `batch_score_prestacked` positive-part 차등 페널티 (B1)

**Files:**
- Modify: `recommendation-server/engine/twostage.py:253-259`
- Test: `recommendation-server/tests/test_twostage.py`

**Interfaces:**
- Consumes: `index._candidate_tier` (Task 7), `SOURCE_TIER_PENALTY` (config).
- Produces: `batch_score_prestacked` 반환 scores에 후보 tier 페널티 적용(양수만). 쿼리책·음수 점수 미변경.

- [ ] **Step 1: 실패 테스트 작성**

```python
# recommendation-server/tests/test_twostage.py 에 추가
def test_batch_score_positive_part_penalty(monkeypatch):
    # provisional 후보 양수 점수는 감점, 음수 점수는 미변경(부호 안전)
    import engine.twostage as ts
    # 최소 index/입력 구성은 기존 test_twostage 헬퍼 재사용. 핵심 단언:
    # scores_with_tier[minimal_cand] == scores_no_tier[minimal_cand] * 0.85  (양수일 때)
    # scores_with_tier[neg_cand]    == scores_no_tier[neg_cand]              (음수일 때)
    ...
```
(기존 test_twostage.py의 픽스처 빌더를 재사용해 동일 입력에 `index._candidate_tier` 유무로 두 번 호출, 위 두 불변 단언.)

- [ ] **Step 2: 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_twostage.py -k positive_part -v`
Expected: FAIL

- [ ] **Step 3: 구현**

```python
# engine/twostage.py — batch_score_prestacked 의 scores[cid] 확정 직후
from config import (... , SOURCE_TIER_PENALTY)  # import 추가

        scores[cid] = (
            w_reason * reason_score + w_desc * desc_score
            + w_l1 * l1_score + w_l2 * l2_score + w_fb_desc * fb_desc_score
        )
        tier = getattr(index, "_candidate_tier", {}).get(cid, "rich")
        pen = SOURCE_TIER_PENALTY.get(tier, 1.0)
        if pen != 1.0 and scores[cid] > 0:   # positive-part: 음수 미변경(부호 안전, B1)
            scores[cid] *= pen
```

- [ ] **Step 4: 통과 확인 + 회귀**

Run: `cd recommendation-server && python -m pytest tests/test_twostage.py tests/test_twostage_augment.py tests/test_recommend_core.py -v`
Expected: PASS (tier 없으면 전부 rich=1.0 → 기존 동치)

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/twostage.py recommendation-server/tests/test_twostage.py
git commit -m "feat: stage2 positive-part 차등 페널티 (B1 부호 안전)"
```

---

### Task 9: 인덱스 빌드 — source_tier SELECT + `_candidate_tier` 운반

**Files:**
- Modify: `recommendation-server/scripts/build_index.py:186-194` (`_fetch_v3_task`), `:258-296` (add 루프)
- Test: `recommendation-server/tests/test_build_index_reasons.py` (또는 신규 `test_build_index_tier.py`)

**Interfaces:**
- Consumes: `book_v3_vectors.source_tier`.
- Produces: 빌드된 `index._candidate_tier` (non-rich 생존자만), 키 ⊆ `bid_order`.

- [ ] **Step 1: 실패 테스트 작성 (add 루프 단위 추출 또는 통합)**

```python
# recommendation-server/tests/test_build_index_tier.py
import numpy as np
from engine.index import VectorIndex

def populate_tier(index, v3_map):
    # build_index 의 tier 운반 로직을 함수로 추출해 테스트(생존자만)
    from scripts.build_index_tier import set_candidate_tiers  # Step3에서 추출
    set_candidate_tiers(index, v3_map)

def test_candidate_tier_subset_of_bid_order():
    idx = VectorIndex(dim=2, dtype=np.float16)
    idx.add_book("a", [], np.array([1,0],np.float32), np.zeros(2,np.float32), np.zeros(2,np.float32))
    v3 = {"a": {"source_tier": "minimal"}, "ghost": {"source_tier": "kakao_desc"}}
    from scripts.build_index import set_candidate_tiers
    set_candidate_tiers(idx, v3)
    assert set(idx._candidate_tier) <= set(idx.book_ids)  # ghost(미add) 제외
    assert idx._candidate_tier["a"] == "minimal"
```

- [ ] **Step 2: 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_build_index_tier.py -v`
Expected: FAIL (set_candidate_tiers 미존재)

- [ ] **Step 3: 구현**

```python
# recommendation-server/scripts/build_index.py
# _fetch_v3_task SELECT 에 source_tier 추가
        raw = _fetch_paginated(
            client, "book_v3_vectors",
            "book_id,desc_embedding,l1_genre_id,l2_genre_id,source_tier",
            PAGE_SIZE_VECTOR, order_col="book_id")

# add 루프 직후 helper 로 tier 운반 (생존자=index._books)
def set_candidate_tiers(index, v3_map):
    """인덱스에 실제 add된 책 중 non-rich tier 만 index._candidate_tier 에 운반."""
    tier_map = {}
    book_ids = set(index.book_ids)
    for bid, v3 in v3_map.items():
        if bid not in book_ids:
            continue
        t = v3.get("source_tier") or "rich"
        if t != "rich":
            tier_map[bid] = t
    index._candidate_tier = tier_map

# build() 의 index.build_desc_matrix() 호출 전에:
    set_candidate_tiers(index, v3_map)
    index.build_desc_matrix()
```

- [ ] **Step 4: 통과 확인**

Run: `cd recommendation-server && python -m pytest tests/test_build_index_tier.py tests/test_build_index_reasons.py tests/test_build_index_atomic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/scripts/build_index.py recommendation-server/tests/test_build_index_tier.py
git commit -m "feat: 인덱스 빌드가 source_tier 를 _candidate_tier 로 운반"
```

---

### Task 10: `reembed_provisional.py` — tier 재도출(라벨 교정) + rich 승격 재임베딩 (C5/M4)

**Files:**
- Create: `scripts/reembed_provisional.py`
- Test: `tests/test_reembed_provisional.py`

**Interfaces:**
- Consumes: `build_desc_source` (Task 5).
- Produces: `source_tier != 'rich'` 행을 재도출. (a) tier 변동(예 backfill 'kakao_desc'→실제 'minimal') 시 **source_text 동일해도 `source_tier`/`provisional` UPDATE**(R3 relabel 보존). (b) source_text 변경(rich 승격 등) 시 재임베딩.

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_reembed_provisional.py
from scripts.reembed_provisional import plan_row_action

def test_relabel_without_reembed_when_text_same_tier_changed():
    # 저장 tier=kakao_desc(backfill), 실제는 minimal, source_text 동일
    action = plan_row_action(stored_tier="kakao_desc", stored_source_text="제목 저자 소설",
                             new_text="제목 저자 소설", new_tier="minimal")
    assert action == {"reembed": False, "update_tier": True, "new_tier": "minimal"}

def test_reembed_when_text_changed_to_rich():
    action = plan_row_action(stored_tier="kakao_desc", stored_source_text="짧은 설명",
                             new_text="가"*250, new_tier="rich")
    assert action == {"reembed": True, "update_tier": True, "new_tier": "rich"}

def test_noop_when_text_and_tier_same():
    action = plan_row_action(stored_tier="minimal", stored_source_text="제목 저자",
                             new_text="제목 저자", new_tier="minimal")
    assert action == {"reembed": False, "update_tier": False, "new_tier": "minimal"}
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_reembed_provisional.py -v`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현 (순수 결정 함수 + 배치 러너)**

```python
# scripts/reembed_provisional.py
"""provisional(non-rich) 행을 재도출: tier 라벨 교정 + rich 승격 시 재임베딩. embed-once."""
def plan_row_action(stored_tier, stored_source_text, new_text, new_tier):
    """순수 결정. source_text 변경 시만 재임베딩(OpenAI), tier 변동 시 항상 라벨 UPDATE(R3)."""
    reembed = bool(new_text) and (new_text != stored_source_text)
    update_tier = (new_tier != stored_tier)
    return {"reembed": reembed, "update_tier": update_tier, "new_tier": new_tier}

# main(): book_v3_vectors(source_tier!='rich')+books 조인 → build_desc_source 재도출 →
#   plan_row_action → reembed면 call_embedding+upsert(desc/source_text/tier/provisional),
#   아니고 update_tier면 source_tier/provisional 만 UPDATE. sleep/배치/체크포인트(배치 규칙).
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_reembed_provisional.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/reembed_provisional.py tests/test_reembed_provisional.py
git commit -m "feat: reembed_provisional — tier 라벨 교정 + rich 승격 재임베딩 (C5/M4)"
```

---

### Task 11: daily-pipeline enrich 에 reembed 배선

**Files:**
- Modify: `.github/workflows/daily-pipeline.yml` (enrich job, v3 vectors step 뒤)

**Interfaces:**
- Consumes: `scripts/reembed_provisional.py`.
- Produces: 스케줄/디스패치 시 rich 확보된 provisional 행 자동 승격. `REEMBED_LIMIT` mode 변수.

- [ ] **Step 1: 워크플로 수정**

```yaml
      # enrich job env 에 추가
      REEMBED_LIMIT: ${{ github.event.inputs.mode == 'small' && '3' || '300' }}
      # v3 vectors step 뒤에 추가
      - name: Reembed provisional (tier 교정 + rich 승격)
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python3 scripts/reembed_provisional.py --limit $REEMBED_LIMIT
```

- [ ] **Step 2: YAML 문법 확인**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-pipeline.yml'))"`
Expected: 에러 없음

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/daily-pipeline.yml
git commit -m "feat: daily enrich 에 reembed_provisional 배선"
```

---

### Task 12: 회귀 — 구 pkl 로드 + dedup 작품단위 rich 생존

**Files:**
- Test: `recommendation-server/tests/test_loader.py`, `recommendation-server/tests/test_dedup.py`

**Interfaces:**
- Consumes: 전 Task 산출물.

- [ ] **Step 1: 실패/회귀 테스트 작성**

```python
# test_loader.py — 구 v4 pkl(또는 _candidate_tier 없는 index)이 로드되고 무감점
def test_v4_index_without_tier_loads_and_no_penalty():
    # 기존 v4 번들 픽스처 로드 → similar_by_vector 가 getattr 폴백으로 무감점 동작
    ...

# test_dedup.py — 같은 작품 rich vs minimal, 페널티 후에도 rich 판본 생존
def test_dedup_keeps_rich_edition_over_thin_same_work():
    # 같은 title+author, rich(score 높음) vs minimal(감점) → dedup 이 rich 유지
    ...
```

- [ ] **Step 2: 실패 확인 → Step 3: 필요한 보정 → Step 4: 통과**

Run: `cd recommendation-server && python -m pytest tests/test_loader.py tests/test_dedup.py -v`
Expected: PASS

- [ ] **Step 5: 전체 스위트 그린**

Run: `cd recommendation-server && python -m pytest -q` 및 `python -m pytest tests/ -q` (루트)
Expected: 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add recommendation-server/tests/test_loader.py recommendation-server/tests/test_dedup.py
git commit -m "test: 구 pkl 무감점 회귀 + dedup rich 판본 생존"
```

---

## Rollout (🔴 COST-GATED — Eden 명시 승인 후, §9 배포 순서)

> 위 Task 1~12는 비용 0(코드+테스트+마이그레이션). 아래는 egress/OpenAI/메모리 게이트 → **각 단계 전 Eden 승인.**

1. **count 보고:** `books` 총수 vs `book_v3_vectors` 총수 → 신규 임베딩 권수 × ~$0.0001 = OpenAI 비용 + 예상 메모리(+권수×4KB) 산정·보고. 메모리 위협 시 books-cap 먼저 결정.
2. **마이그레이션 머지** → apply-migrations 자동(source_tier + backfill).
3. **코드 배포** → recommendation-server push → Render(신코드 getattr 폴백으로 현 v4 인덱스 무크래시).
4. **mode=small 실쓰기 검증** → daily-pipeline dispatch(small): generate_book_v3_vectors + reembed 소량 → kakao_desc/minimal 행·tier 실생성 확인(dry-run 한계 회피).
5. **전량 embed-once** → daily-pipeline full(승인 후).
6. **인덱스 재빌드**(수동 dispatch) → RSS 게이트 통과(OOM 0) → pkl 커밋/배포.
7. **prod E2E**(throwaway): rich 없는 책만 좋아요 6권 → /recommend 비어있지 않음 + niche-thin > 평범-rich 가드 + tier1 similar 비지 않음.

## Self-Review (작성자 체크)
- **Spec 커버리지:** C1(Task 4·5)·C2(Task 6)·C3(Task 1·2·9)·C4(Task 7·8)·C5(Task 10·11) + 회귀(Task 12) + Rollout(§9). 누락 없음.
- **Placeholder:** Task 8·12 테스트는 기존 픽스처 빌더 재사용이 필요해 본문에 "..." 표기 — 구현 시 해당 파일의 기존 헬퍼로 채움(완전 신규 코드 아님, 회귀 성격). 그 외 전 코드 실체 제공.
- **타입 일관성:** `(text, tier)` 튜플·`_candidate_tier: dict[str,str]`·`SOURCE_TIER_PENALTY` 키 전 Task 일치. tier 문자열 3값 Global Constraints 고정.
