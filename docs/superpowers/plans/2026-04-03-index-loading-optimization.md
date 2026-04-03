# Index Loading Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supabase REST 의존을 제거하고, build-time pkl + float16 + 단일 워커로 추천 서버 안정화

**Architecture:** 로컬에서 `build_index.py`를 실행하여 Supabase → VectorIndex → pkl 파일 생성. 서버는 pkl만 로드. 모든 벡터는 float16으로 저장하여 메모리 50% 절감. Dockerfile은 워커 1개로 변경.

**Tech Stack:** Python 3.11, numpy (float16), pickle, FastAPI, Supabase REST (빌드 시에만)

**Spec:** `docs/superpowers/specs/2026-04-03-index-loading-optimization-design.md`

---

## File Structure

| 파일 | 역할 | 변경 |
|------|------|------|
| `engine/index.py` | VectorIndex — float16 벡터 저장 | 수정 |
| `scripts/build_index.py` | Supabase → pkl 빌드 스크립트 | 신규 |
| `engine/loader.py` | pkl 파일 로드 + `_to_np` 유틸 | 교체 |
| `main.py` | FastAPI 앱 + 풍부한 health | 수정 |
| `Dockerfile` | 워커 1개, data/ COPY | 수정 |
| `.gitignore` | data/*.pkl 제외 | 신규 |
| `tests/test_index.py` | float16 테스트 추가 | 수정 |
| `tests/test_loader.py` | pkl 로드 테스트 | 신규 |

---

### Task 1: VectorIndex float16 지원

**Files:**
- Modify: `engine/index.py`
- Modify: `tests/test_index.py`

- [ ] **Step 1: float16 테스트 작성**

`tests/test_index.py`에 추가:

```python
def test_float16_storage_and_similarity(self):
    """float16으로 저장해도 cosine sim 결과가 float32와 거의 동일."""
    idx32 = VectorIndex(dim=4)
    idx16 = VectorIndex(dim=4, dtype=np.float16)
    desc = _norm([1, 0.5, 0.2, 0])
    l1 = _norm([1, 0, 0, 0])
    l2 = _norm([0, 1, 0, 0])
    reasons = [_norm([1, 0.3, 0, 0]), _norm([0, 1, 0.5, 0])]

    idx32.add_book("b1", reasons=reasons, desc=desc, l1=l1, l2=l2)
    idx16.add_book("b1", reasons=reasons, desc=desc, l1=l1, l2=l2)

    bv32 = idx32.get_book("b1")
    bv16 = idx16.get_book("b1")
    assert bv32.desc.dtype == np.float32
    assert bv16.desc.dtype == np.float16
    # 유사도 차이 < 0.01
    sim32 = float(np.dot(bv32.desc.astype(np.float32), bv32.l1.astype(np.float32)))
    sim16 = float(np.dot(bv16.desc.astype(np.float32), bv16.l1.astype(np.float32)))
    assert abs(sim32 - sim16) < 0.01

def test_float16_similar_by_desc(self):
    """float16 인덱스에서 similar_by_desc가 동일한 순위를 반환."""
    idx = VectorIndex(dim=4, dtype=np.float16)
    idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                 l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
    idx.add_book("b2", reasons=[], desc=_norm([0.9, 0.1, 0, 0]),
                 l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
    idx.add_book("b3", reasons=[], desc=_norm([0, 0, 1, 0]),
                 l1=_norm([0, 0, 1, 0]), l2=_norm([0, 0, 0, 1]))
    idx.build_desc_matrix()
    sims = idx.similar_by_desc("b1", limit=2)
    assert sims[0][0] == "b2"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_index.py -v -k "float16"`
Expected: FAIL — `VectorIndex.__init__()` got unexpected keyword argument 'dtype'

- [ ] **Step 3: VectorIndex에 dtype 파라미터 추가**

`engine/index.py` 수정:

```python
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class BookVectors:
    reasons: list[np.ndarray]
    desc: np.ndarray
    l1: np.ndarray
    l2: np.ndarray


class VectorIndex:
    """벡터 저장 + 검색 인덱스. 모든 벡터는 L2-정규화 가정."""

    def __init__(self, dim: int = 2000, dtype=np.float32):
        self.dim = dim
        self.dtype = dtype
        self._books: dict[str, BookVectors] = {}
        self._desc_matrix: Optional[np.ndarray] = None
        self._desc_bid_order: list[str] = []

    @property
    def book_ids(self) -> list[str]:
        return list(self._books.keys())

    def add_book(self, book_id: str, reasons: list[np.ndarray],
                 desc: np.ndarray, l1: np.ndarray, l2: np.ndarray):
        self._books[book_id] = BookVectors(
            reasons=[r.astype(self.dtype) for r in reasons],
            desc=desc.astype(self.dtype),
            l1=l1.astype(self.dtype),
            l2=l2.astype(self.dtype),
        )
        self._desc_matrix = None

    def get_book(self, book_id: str) -> Optional[BookVectors]:
        return self._books.get(book_id)

    @staticmethod
    def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def build_desc_matrix(self):
        self._desc_bid_order = list(self._books.keys())
        descs = [self._books[bid].desc for bid in self._desc_bid_order]
        self._desc_matrix = np.stack(descs)

    def similar_by_desc(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        if self._desc_matrix is None:
            self.build_desc_matrix()
        bv = self._books.get(book_id)
        if bv is None:
            return []
        scores = self._desc_matrix @ bv.desc
        idx_self = self._desc_bid_order.index(book_id)
        scores[idx_self] = -999
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i])) for i in top_idx]
```

핵심 변경: `__init__`에 `dtype` 파라미터 추가, `add_book`에서 `self.dtype`으로 캐스팅. 기본값 `float32`라서 기존 테스트 호환.

- [ ] **Step 4: 전체 index 테스트 실행**

Run: `cd recommendation-server && python -m pytest tests/test_index.py -v`
Expected: ALL PASS (기존 6개 + 새 2개)

- [ ] **Step 5: scorer 테스트도 통과 확인**

Run: `cd recommendation-server && python -m pytest tests/test_scorer.py -v`
Expected: ALL PASS (float32 기본값이라 영향 없음)

- [ ] **Step 6: 커밋**

```bash
git add engine/index.py tests/test_index.py
git commit -m "feat: VectorIndex float16 dtype 지원"
```

---

### Task 2: pkl 저장/로드 유틸리티 + 테스트

**Files:**
- Create: `tests/test_loader.py`
- Modify: `engine/loader.py`

- [ ] **Step 1: pkl 로드 테스트 작성**

`tests/test_loader.py` 생성:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pickle
import tempfile
import numpy as np
import pytest
from engine.index import VectorIndex
from engine.loader import load_index, _to_np


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestPklLoader:
    def test_load_index_from_pkl(self, tmp_path):
        """pkl 번들에서 index + meta를 정상 로드."""
        idx = VectorIndex(dim=4, dtype=np.float16)
        idx.add_book("b1", reasons=[_norm([1, 0, 0, 0])],
                     desc=_norm([1, 0, 0, 0]), l1=_norm([1, 0, 0, 0]),
                     l2=_norm([0, 1, 0, 0]))
        idx.build_desc_matrix()

        meta = {"b1": {"title": "Test", "author": "A", "cover_url": None}}
        bundle = {
            "index": idx,
            "meta": meta,
            "built_at": "2026-04-03T12:00:00",
            "version": "v3-float16",
        }

        pkl_path = tmp_path / "index.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(bundle, f)

        loaded_idx, loaded_meta, loaded_built_at = load_index(str(pkl_path))
        assert len(loaded_idx.book_ids) == 1
        assert loaded_meta["b1"]["title"] == "Test"
        assert loaded_built_at == "2026-04-03T12:00:00"

    def test_load_index_missing_file(self):
        """존재하지 않는 pkl 파일이면 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_index("/nonexistent/path.pkl")

    def test_load_index_invalid_version(self, tmp_path):
        """version이 다르면 ValueError."""
        bundle = {"index": None, "meta": {}, "built_at": "", "version": "v2-old"}
        pkl_path = tmp_path / "index.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(bundle, f)
        with pytest.raises(ValueError, match="version"):
            load_index(str(pkl_path))


class TestToNp:
    def test_string_vector(self):
        result = _to_np("[1.0, 0.0, 0.0]")
        assert result.shape == (3,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_list_vector(self):
        result = _to_np([3.0, 4.0])
        assert result.shape == (2,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_zero_vector(self):
        result = _to_np([0.0, 0.0, 0.0])
        assert np.allclose(result, 0.0)
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && python -m pytest tests/test_loader.py -v`
Expected: FAIL — `load_index() takes 0 positional arguments but 1 was given`

- [ ] **Step 3: loader.py를 pkl 기반으로 교체**

`engine/loader.py` 전체 교체:

```python
# recommendation-server/engine/loader.py
"""서버 시작 시 로컬 pkl 파일에서 VectorIndex를 로드한다.

빌드: scripts/build_index.py로 생성된 data/index.pkl 사용.
"""
import os
import pickle
import numpy as np

EXPECTED_VERSION = "v3-float16"
DEFAULT_PKL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")


def _to_np(vec) -> np.ndarray:
    """DB 벡터(리스트 또는 문자열)를 L2-정규화된 numpy float32로 변환."""
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def load_index(pkl_path: str = DEFAULT_PKL_PATH):
    """pkl 번들에서 VectorIndex + books_meta + built_at 로드.

    Returns:
        tuple: (VectorIndex, books_meta dict, built_at str)
    """
    if not os.path.exists(pkl_path):
        raise FileNotFoundError(f"Index pkl not found: {pkl_path}")

    with open(pkl_path, "rb") as f:
        bundle = pickle.load(f)

    version = bundle.get("version", "")
    if version != EXPECTED_VERSION:
        raise ValueError(
            f"Index version mismatch: expected '{EXPECTED_VERSION}', got '{version}'"
        )

    return bundle["index"], bundle["meta"], bundle["built_at"]
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd recommendation-server && python -m pytest tests/test_loader.py -v`
Expected: ALL PASS (6개)

- [ ] **Step 5: 커밋**

```bash
git add engine/loader.py tests/test_loader.py
git commit -m "feat: loader를 pkl 기반으로 교체"
```

---

### Task 3: main.py — lifespan + health 업데이트

**Files:**
- Modify: `main.py`

- [ ] **Step 1: main.py 수정**

`main.py`에서 `load_index`의 새 시그니처(3개 반환값) 반영 + health 풍부화:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.recommend import router as recommend_router
from api.similar import router as similar_router
from api.feedback import router as feedback_router

app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from engine.loader import load_index
    index, books_meta, built_at = load_index()
    app_state["index"] = index
    app_state["books_meta"] = books_meta
    app_state["built_at"] = built_at
    print(f"[main] Server ready. {len(index.book_ids)} books in index. Built at {built_at}")
    yield
    app_state.clear()


app = FastAPI(title="Curation Recommendation Server", lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(similar_router)
app.include_router(feedback_router)


@app.get("/health")
async def health():
    index = app_state.get("index")
    total_reasons = 0
    if index:
        for bv in index._books.values():
            total_reasons += len(bv.reasons)
    return {
        "status": "ok",
        "books_loaded": len(index.book_ids) if index else 0,
        "total_reasons": total_reasons,
        "index_built_at": app_state.get("built_at", ""),
        "version": "v3-float16",
    }
```

- [ ] **Step 2: 커밋**

```bash
git add main.py
git commit -m "feat: health 풍부화 + load_index 3-tuple 반영"
```

---

### Task 4: build_index.py — 빌드 스크립트

**Files:**
- Create: `scripts/build_index.py`

- [ ] **Step 1: build_index.py 생성**

`scripts/build_index.py`:

```python
#!/usr/bin/env python3
"""Supabase에서 벡터 데이터를 로드하여 data/index.pkl 생성.

사용법: cd recommendation-server && python scripts/build_index.py
결과물: data/index.pkl (~170MB, float16)
"""
import os
import sys
import time
import pickle
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from engine.index import VectorIndex
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, EMBEDDING_DIMENSIONS

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "index.pkl")
PAGE_SIZE_META = 1000
PAGE_SIZE_VECTOR = 500
MAX_RETRIES = 3
RETRY_BACKOFF = 10
PAGE_SLEEP = 1


def _to_np(vec) -> np.ndarray:
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def _fetch_paginated(sb, table: str, select: str, page_size: int,
                     order_col: str = "id", filters=None) -> list:
    all_rows = []
    offset = 0
    while True:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                q = sb.table(table).select(select).order(order_col).range(
                    offset, offset + page_size - 1)
                if filters:
                    for col, condition in filters.items():
                        q = q.filter(col, *condition)
                rows = q.execute().data
                break
            except Exception as e:
                print(f"  [retry {attempt}/{MAX_RETRIES}] {e}")
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BACKOFF * attempt)
        all_rows.extend(rows)
        print(f"  page {offset // page_size + 1}: {len(rows)} rows (total: {len(all_rows)})")
        if len(rows) < page_size:
            break
        offset += page_size
        time.sleep(PAGE_SLEEP)
    return all_rows


def build():
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1. books meta
    print("[build] Loading books meta...")
    books_raw = _fetch_paginated(sb, "books", "id,title,author,cover_url", PAGE_SIZE_META)
    books_meta = {}
    for b in books_raw:
        books_meta[b["id"]] = {
            "title": b["title"], "author": b["author"],
            "cover_url": b.get("cover_url"),
        }
    print(f"  → {len(books_meta)} books")

    # 2. genre embeddings
    print("[build] Loading genre embeddings...")
    genres_raw = _fetch_paginated(sb, "genre_embeddings", "id,embedding", PAGE_SIZE_VECTOR)
    genre_embs = {}
    for g in genres_raw:
        emb = _to_np(g["embedding"])
        assert emb.shape[0] == EMBEDDING_DIMENSIONS, \
            f"genre dim mismatch: {emb.shape[0]} != {EMBEDDING_DIMENSIONS}"
        genre_embs[g["id"]] = emb
    print(f"  → {len(genre_embs)} genres")

    # 3. v3 vectors
    print("[build] Loading v3 vectors...")
    v3_raw = _fetch_paginated(
        sb, "book_v3_vectors", "book_id,desc_embedding,l1_genre_id,l2_genre_id",
        PAGE_SIZE_VECTOR, order_col="book_id")
    v3_map = {}
    for v in v3_raw:
        v3_map[v["book_id"]] = v
    print(f"  → {len(v3_map)} v3 vectors")

    # 4. reason embeddings
    print("[build] Loading reason embeddings...")
    reasons_raw = _fetch_paginated(
        sb, "book_love_reasons", "book_id,reason_embedding",
        PAGE_SIZE_VECTOR,
        filters={"reason_embedding": ("not.is", "null")})
    reasons_by_book = {}
    for r in reasons_raw:
        if r.get("reason_embedding") is not None:
            bid = r["book_id"]
            emb = _to_np(r["reason_embedding"])
            assert emb.shape[0] == EMBEDDING_DIMENSIONS, \
                f"reason dim mismatch: {emb.shape[0]} != {EMBEDDING_DIMENSIONS}"
            if bid not in reasons_by_book:
                reasons_by_book[bid] = []
            reasons_by_book[bid].append(emb)
    total_reasons = sum(len(v) for v in reasons_by_book.values())
    print(f"  → {total_reasons} reasons across {len(reasons_by_book)} books")

    # 5. VectorIndex 구축 (float16)
    print("[build] Building VectorIndex (float16)...")
    index = VectorIndex(dim=EMBEDDING_DIMENSIONS, dtype=np.float16)
    loaded = 0
    skipped = 0
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            skipped += 1
            continue
        l1_id, l2_id = v3.get("l1_genre_id"), v3.get("l2_genre_id")
        if not l1_id or not l2_id or l1_id not in genre_embs or l2_id not in genre_embs:
            skipped += 1
            continue
        desc_emb = v3.get("desc_embedding")
        if not desc_emb:
            skipped += 1
            continue
        desc_np = _to_np(desc_emb)
        assert desc_np.shape[0] == EMBEDDING_DIMENSIONS, \
            f"desc dim mismatch: {desc_np.shape[0]} != {EMBEDDING_DIMENSIONS}"
        index.add_book(
            bid,
            reasons=reasons_by_book.get(bid, []),
            desc=desc_np,
            l1=genre_embs[l1_id],
            l2=genre_embs[l2_id],
        )
        loaded += 1

    index.build_desc_matrix()
    print(f"  → {loaded} books loaded, {skipped} skipped")

    # 6. pkl 저장
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    built_at = datetime.now(timezone.utc).isoformat()
    bundle = {
        "index": index,
        "meta": books_meta,
        "built_at": built_at,
        "version": "v3-float16",
    }
    with open(OUTPUT_PATH, "wb") as f:
        pickle.dump(bundle, f)

    size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    print(f"\n[build] Done! {OUTPUT_PATH}")
    print(f"  size: {size_mb:.1f} MB")
    print(f"  books: {loaded}")
    print(f"  reasons: {total_reasons}")
    print(f"  built_at: {built_at}")
    print(f"  version: v3-float16")


if __name__ == "__main__":
    build()
```

- [ ] **Step 2: data 디렉토리 준비 + .gitignore**

`.gitignore` 생성 (`recommendation-server/.gitignore`):

```
data/*.pkl
.venv/
__pycache__/
.env
```

- [ ] **Step 3: 커밋**

```bash
git add scripts/build_index.py .gitignore
git commit -m "feat: build_index.py — Supabase → pkl 빌드 스크립트"
```

---

### Task 5: Dockerfile 업데이트

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Dockerfile 수정**

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

변경: `--workers 4` → `--workers 1`
(`COPY . .`는 이미 data/ 포함. .gitignore는 Docker에 영향 없음)

- [ ] **Step 2: 커밋**

```bash
git add Dockerfile
git commit -m "fix: Dockerfile 워커 4→1 (메모리 최적화)"
```

---

### Task 6: 통합 검증 — pkl 빌드 + 서버 기동

**Files:** 없음 (수동 검증)

- [ ] **Step 1: pkl 빌드 실행**

```bash
cd recommendation-server
python scripts/build_index.py
```

Expected output:
```
[build] Loading books meta...
  → 8610 books
[build] Loading genre embeddings...
  → 825 genres
[build] Loading v3 vectors...
  → 2510 v3 vectors
[build] Loading reason embeddings...
  → 33824 reasons across 2535 books
[build] Building VectorIndex (float16)...
  → 2510 books loaded, 0 skipped
[build] Done! .../data/index.pkl
  size: ~170 MB
  books: 2510
  reasons: 33824
  version: v3-float16
```

- [ ] **Step 2: pkl 파일 크기 확인**

```bash
ls -lh data/index.pkl
```

Expected: ~150-180 MB

- [ ] **Step 3: 서버 로컬 기동**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Expected: 2초 이내에 `Server ready. 2510 books in index.` 출력

- [ ] **Step 4: health 엔드포인트 확인**

```bash
curl http://localhost:8000/health
```

Expected:
```json
{
  "status": "ok",
  "books_loaded": 2510,
  "total_reasons": 33824,
  "index_built_at": "2026-04-03T...",
  "version": "v3-float16"
}
```

- [ ] **Step 5: similar 엔드포인트 확인**

아무 book_id로 테스트 (JWT 없이 테스트하려면 auth 우회 필요 — 이전 테스트에서 사용한 book_id 사용):

```bash
# health로 서버 정상 확인되면 OK. similar는 JWT 필요하므로 유닛 테스트로 커버.
```

- [ ] **Step 6: 전체 테스트 실행**

```bash
cd recommendation-server && python -m pytest tests/ -v
```

Expected: ALL PASS

- [ ] **Step 7: 커밋**

```bash
git add -A
git commit -m "chore: 통합 검증 완료 — pkl 빌드 + 서버 기동 확인"
```

---

## 주의사항

- `api/recommend.py:6`에서 `from engine.loader import _to_np`를 사용 중 — `_to_np`는 새 loader.py에도 유지됨
- `build_index.py`는 `recommendation-server/` 디렉토리에서 실행해야 함 (`.env` 경로 의존)
- `data/index.pkl`은 `.gitignore`에 포함 — git에 추적되지 않음. Docker build 전에 로컬에 존재해야 함
- 기존 테스트(test_index 6개, test_scorer 5개)는 VectorIndex 기본 dtype=float32라서 영향 없음
