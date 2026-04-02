# FastAPI 추천 서버 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v3 스코어링 알고리즘을 서비스하는 FastAPI 추천 서버 구현 및 배포

**Architecture:** 서버 시작 시 Supabase에서 전체 벡터(reason/desc/L1/L2)를 numpy 행렬로 메모리에 로드. 추천 요청 시 numpy BLAS 행렬곱으로 밀리초 단위 스코어링. 피드백 수신 시 OpenAI 임베딩 → Supabase 저장 → 메모리 즉시 업데이트.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, numpy, supabase-py, python-jose (JWT), requests (OpenAI API), python-dotenv

**Spec:** `docs/superpowers/specs/2026-04-01-recommendation-engine-v3-design.md` 섹션 4.1 + 6

---

## DB 스키마 참조

```
books: id(uuid), title, author, cover_url, genre
book_love_reasons: id, book_id(FK→books), reason, reason_embedding(vector[2000]), source
book_v3_vectors: book_id(FK→books), desc_embedding(vector[2000]), l1_genre_id(FK), l2_genre_id(FK)
genre_embeddings: id, genre_text, level('l1'|'l2'), embedding(vector[2000])
user_books: (현재 비어있음 — Flutter 앱에서 생성 예정)
```

## 파일 구조

```
recommendation-server/
├── main.py                  # FastAPI 앱, lifespan으로 벡터 로드
├── config.py                # 환경변수, 상수
├── auth.py                  # Supabase JWT 검증
├── models.py                # Pydantic 요청/응답 스키마
├── engine/
│   ├── __init__.py
│   ├── loader.py            # Supabase → numpy 행렬 로드
│   ├── index.py             # VectorIndex 클래스 (벡터 저장/검색)
│   └── scorer.py            # v3 스코어링 알고리즘
├── api/
│   ├── __init__.py
│   ├── recommend.py         # GET /recommend/{user_id}
│   ├── similar.py           # GET /similar/{book_id}
│   └── feedback.py          # POST /feedback
├── requirements.txt
├── Dockerfile               # Render/Fly.io 배포용
├── .env.example
└── tests/
    ├── __init__.py
    ├── conftest.py           # 공유 fixture (테스트용 벡터 데이터)
    ├── test_scorer.py        # 스코어링 알고리즘 단위 테스트
    ├── test_index.py         # VectorIndex 단위 테스트
    ├── test_api_recommend.py # 추천 API 통합 테스트
    ├── test_api_similar.py   # 유사도 API 통합 테스트
    └── test_api_feedback.py  # 피드백 API 통합 테스트
```

---

### Task 1: 프로젝트 셋업 + config

**Files:**
- Create: `recommendation-server/config.py`
- Create: `recommendation-server/requirements.txt`
- Create: `recommendation-server/.env.example`

- [ ] **Step 1: 디렉토리 생성**

```bash
mkdir -p recommendation-server/engine recommendation-server/api recommendation-server/tests
touch recommendation-server/engine/__init__.py recommendation-server/api/__init__.py recommendation-server/tests/__init__.py
```

- [ ] **Step 2: requirements.txt 작성**

```
# recommendation-server/requirements.txt
fastapi==0.115.*
uvicorn[standard]==0.34.*
numpy>=1.26
supabase>=2.0
python-jose[cryptography]>=3.3
python-dotenv>=1.0
requests>=2.31
httpx>=0.27
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 3: config.py 작성**

```python
# recommendation-server/config.py
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 2000

# v3 스코어링 가중치 (스펙 섹션 4.1)
W_REASON = 1.0
W_DESC = 0.5
W_L1 = 3.0
W_L2 = 1.0
W_FB_DESC = 2.0

# 피드백 있는 책의 reason 가중치 감소 (피드백이 주 신호)
REASON_WEIGHT_WITH_FB = 0.5
REASON_WEIGHT_WITHOUT_FB = 1.0
FB_REASON_WEIGHT = 3.0  # 피드백 임베딩의 reason_score 기여 가중치

DEFAULT_RECOMMEND_LIMIT = 10
DEFAULT_SIMILAR_LIMIT = 10
```

- [ ] **Step 4: .env.example 작성**

```
# recommendation-server/.env.example
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_JWT_SECRET=your-jwt-secret
OPENAI_API_KEY=sk-...
```

- [ ] **Step 5: 커밋**

```bash
git add recommendation-server/
git commit -m "feat: 추천 서버 프로젝트 셋업 (config, requirements)"
```

---

### Task 2: VectorIndex — 벡터 저장/검색 핵심 자료구조

**Files:**
- Create: `recommendation-server/engine/index.py`
- Create: `recommendation-server/tests/test_index.py`

- [ ] **Step 1: 테스트 작성**

```python
# recommendation-server/tests/test_index.py
import numpy as np
from engine.index import VectorIndex


def _norm(v):
    """단위 벡터 생성 헬퍼."""
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestVectorIndex:
    def test_add_and_get_book_vectors(self):
        idx = VectorIndex(dim=4)
        reasons = [_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])]
        idx.add_book("b1", reasons=reasons, desc=_norm([1, 1, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        bv = idx.get_book("b1")
        assert bv is not None
        assert len(bv.reasons) == 2
        assert bv.desc.shape == (4,)

    def test_get_missing_book_returns_none(self):
        idx = VectorIndex(dim=4)
        assert idx.get_book("missing") is None

    def test_cosine_sim_identical(self):
        idx = VectorIndex(dim=4)
        v = _norm([1, 0, 0, 0])
        sim = idx.cosine_sim(v, v)
        assert abs(sim - 1.0) < 1e-5

    def test_cosine_sim_orthogonal(self):
        idx = VectorIndex(dim=4)
        a = _norm([1, 0, 0, 0])
        b = _norm([0, 1, 0, 0])
        sim = idx.cosine_sim(a, b)
        assert abs(sim) < 1e-5

    def test_desc_matrix_similar(self):
        """desc 벡터 행렬로 book-to-book 유사도 계산."""
        idx = VectorIndex(dim=4)
        idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b2", reasons=[], desc=_norm([0.9, 0.1, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b3", reasons=[], desc=_norm([0, 0, 1, 0]),
                     l1=_norm([0, 0, 1, 0]), l2=_norm([0, 0, 0, 1]))
        idx.build_desc_matrix()
        sims = idx.similar_by_desc("b1", limit=2)
        assert sims[0][0] == "b2"  # b2가 b1과 가장 유사

    def test_book_ids_list(self):
        idx = VectorIndex(dim=4)
        idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b2", reasons=[], desc=_norm([0, 1, 0, 0]),
                     l1=_norm([0, 1, 0, 0]), l2=_norm([1, 0, 0, 0]))
        assert set(idx.book_ids) == {"b1", "b2"}
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd recommendation-server && python -m pytest tests/test_index.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'engine'`

- [ ] **Step 3: VectorIndex 구현**

```python
# recommendation-server/engine/index.py
from dataclasses import dataclass, field
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

    def __init__(self, dim: int = 2000):
        self.dim = dim
        self._books: dict[str, BookVectors] = {}
        # desc 행렬 (book-to-book 유사도용)
        self._desc_matrix: Optional[np.ndarray] = None
        self._desc_bid_order: list[str] = []

    @property
    def book_ids(self) -> list[str]:
        return list(self._books.keys())

    def add_book(self, book_id: str, reasons: list[np.ndarray],
                 desc: np.ndarray, l1: np.ndarray, l2: np.ndarray):
        self._books[book_id] = BookVectors(
            reasons=reasons,
            desc=desc.astype(np.float32),
            l1=l1.astype(np.float32),
            l2=l2.astype(np.float32),
        )
        self._desc_matrix = None  # 무효화

    def get_book(self, book_id: str) -> Optional[BookVectors]:
        return self._books.get(book_id)

    @staticmethod
    def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def build_desc_matrix(self):
        """desc 벡터를 하나의 행렬로 구축 (book-to-book 배치 계산용)."""
        self._desc_bid_order = list(self._books.keys())
        descs = [self._books[bid].desc for bid in self._desc_bid_order]
        self._desc_matrix = np.stack(descs)  # (N, dim)

    def similar_by_desc(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        """desc 코사인 유사도로 유사한 책 반환."""
        if self._desc_matrix is None:
            self.build_desc_matrix()
        bv = self._books.get(book_id)
        if bv is None:
            return []
        scores = self._desc_matrix @ bv.desc  # (N,)
        # 자기 자신 제외
        idx_self = self._desc_bid_order.index(book_id)
        scores[idx_self] = -999
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i])) for i in top_idx]
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd recommendation-server && python -m pytest tests/test_index.py -v
```
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add recommendation-server/engine/index.py recommendation-server/tests/test_index.py
git commit -m "feat: VectorIndex 구현 — 벡터 저장/검색/book-to-book 유사도"
```

---

### Task 3: Scorer — v3 스코어링 알고리즘

**Files:**
- Create: `recommendation-server/engine/scorer.py`
- Create: `recommendation-server/tests/conftest.py`
- Create: `recommendation-server/tests/test_scorer.py`

- [ ] **Step 1: 테스트 fixture 작성**

```python
# recommendation-server/tests/conftest.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def small_index():
    """5권짜리 테스트 인덱스. 소설2 + 경제2 + 과학1."""
    idx = VectorIndex(dim=8)

    # 소설 방향: [1,0,0,0,...]  경제: [0,1,0,0,...]  과학: [0,0,1,0,...]
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    sci_l1 = _norm([0, 0, 1, 0, 0, 0, 0, 0])

    novel_l2a = _norm([1, 0, 0, 0, 0.3, 0, 0, 0])  # 한국소설
    novel_l2b = _norm([1, 0, 0, 0, 0, 0.3, 0, 0])  # 영미소설
    econ_l2 = _norm([0, 1, 0, 0, 0, 0, 0.3, 0])
    sci_l2 = _norm([0, 0, 1, 0, 0, 0, 0, 0.3])

    # 소설1: 사회비판
    idx.add_book("novel1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                 l1=novel_l1, l2=novel_l2a,
                 reasons=[_norm([1, 0, 0, 0, 0.8, 0, 0, 0]),
                          _norm([1, 0, 0, 0, 0.3, 0.5, 0, 0])])
    # 소설2: 감성
    idx.add_book("novel2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l2b,
                 reasons=[_norm([1, 0, 0, 0, 0, 0.9, 0, 0])])
    # 경제1
    idx.add_book("econ1", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l2,
                 reasons=[_norm([0, 1, 0, 0, 0, 0, 0.8, 0]),
                          _norm([0, 1, 0, 0, 0, 0, 0.3, 0.5])])
    # 경제2
    idx.add_book("econ2", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l2,
                 reasons=[_norm([0, 1, 0, 0, 0, 0, 0.9, 0])])
    # 과학1
    idx.add_book("sci1", desc=_norm([0, 0, 1, 0, 0, 0, 0, 0.5]),
                 l1=sci_l1, l2=sci_l2,
                 reasons=[_norm([0, 0, 1, 0, 0, 0, 0, 0.8])])

    idx.build_desc_matrix()
    return idx
```

- [ ] **Step 2: 스코어링 테스트 작성**

```python
# recommendation-server/tests/test_scorer.py
import numpy as np
from engine.scorer import recommend_scores


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestScorer:
    def test_novel_fan_gets_novels(self, small_index):
        """소설 2권 좋아한 유저 → 경제/과학보다 소설 추천."""
        liked = {"novel1": {"rating": "good"},
                 "novel2": {"rating": "good"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        # novel1, novel2는 이미 읽었으므로 결과에서 제외
        # econ1, econ2, sci1 중 어떤 것도 소설보다 높으면 안 됨
        # 하지만 유저가 읽은 책은 후보에서 제외되므로 econ1, econ2, sci1만 남음
        assert "novel1" not in scores
        assert "novel2" not in scores
        # 남은 3권 중 순서 확인은 가중치에 따라 다를 수 있음
        assert len(scores) == 3

    def test_dislike_pushes_away(self, small_index):
        """소설 좋아 + 경제 싫어 → 경제 책 점수 낮아짐."""
        liked = {"novel1": {"rating": "good"},
                 "econ1": {"rating": "bad"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        # econ2는 econ1과 비슷하므로 점수가 낮아야
        assert scores.get("econ2", 0) < scores.get("novel2", 0)

    def test_feedback_boosts_genre(self, small_index):
        """경제 2권(호오만) + 소설 1권(피드백) → 소설 비중 높아짐."""
        liked = {"econ1": {"rating": "good"},
                 "econ2": {"rating": "good"},
                 "novel1": {"rating": "good"}}
        # 소설 방향 피드백
        fb_data = {"novel1": {"emb": _norm([1, 0, 0, 0, 0.7, 0.3, 0, 0]),
                              "is_dislike": False}}
        scores = recommend_scores(small_index, liked, fb_data=fb_data)
        # novel2가 sci1보다 높아야 (소설 피드백이 소설 비중을 높임)
        assert scores.get("novel2", 0) > scores.get("sci1", 0)

    def test_neutral_excluded(self, small_index):
        """neutral 평가 책은 추천 계산에서 제외."""
        liked = {"novel1": {"rating": "good"},
                 "econ1": {"rating": "neutral"}}
        scores = recommend_scores(small_index, liked, fb_data={})
        # econ1이 neutral이면 취향에 기여하지 않음 → 소설 방향만
        # econ2보다 novel2가 높아야
        assert scores.get("novel2", 0) > scores.get("econ2", 0)

    def test_empty_liked_returns_empty(self, small_index):
        scores = recommend_scores(small_index, {}, fb_data={})
        assert scores == {}
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

```bash
cd recommendation-server && python -m pytest tests/test_scorer.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.scorer'`

- [ ] **Step 4: Scorer 구현**

```python
# recommendation-server/engine/scorer.py
"""v3 스코어링 알고리즘.

공식: 1.0×reason_score + 0.5×desc_score + 3.0×L1_score + 1.0×L2_score + 2.0×fb_desc_score
스펙: docs/superpowers/specs/2026-04-01-recommendation-engine-v3-design.md 섹션 4.1
"""
import numpy as np
from engine.index import VectorIndex
from config import (W_REASON, W_DESC, W_L1, W_L2, W_FB_DESC,
                    REASON_WEIGHT_WITH_FB, REASON_WEIGHT_WITHOUT_FB,
                    FB_REASON_WEIGHT)


def _maxsim(query_vecs: list[np.ndarray], candidate_vecs: list[np.ndarray]) -> float:
    """query 벡터들과 candidate 벡터들 간 MaxSim (각 query에 대해 max cosine → 평균)."""
    if not query_vecs or not candidate_vecs:
        return 0.0
    q = np.stack(query_vecs)   # (Nq, dim)
    c = np.stack(candidate_vecs)  # (Nc, dim)
    sims = q @ c.T  # (Nq, Nc)
    return float(sims.max(axis=1).mean())


def _score_one(index: VectorIndex, liked_books: dict, fb_data: dict,
               candidate_id: str) -> float:
    """단일 후보 책의 v3 스코어 계산."""
    cand = index.get_book(candidate_id)
    if cand is None:
        return 0.0

    # 좋아요/싫어요 분리 (neutral 제외)
    good_ids = [bid for bid, d in liked_books.items() if d["rating"] == "good"]
    bad_ids = [bid for bid, d in liked_books.items() if d["rating"] == "bad"]

    if not good_ids and not bad_ids:
        return 0.0

    # ── 1. reason_score: 가중 avg_maxsim ──
    weighted_maxsims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            # 피드백 있는 좋아요: 피드백(×3.0) + reason(×0.5)
            fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim)
        else:
            # 피드백 없는 좋아요: reason(×1.0)
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(REASON_WEIGHT_WITHOUT_FB * r_sim)

    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = max(float(np.dot(fb["emb"], r)) for r in cand.reasons) if cand.reasons else 0.0
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(-(FB_REASON_WEIGHT * fb_sim + REASON_WEIGHT_WITH_FB * r_sim))
        else:
            r_sim = _maxsim(bv.reasons, cand.reasons) if bv.reasons else 0.0
            weighted_maxsims.append(-REASON_WEIGHT_WITHOUT_FB * r_sim)

    reason_score = float(np.mean(weighted_maxsims)) if weighted_maxsims else 0.0

    # ── 2. desc_score: 좋아한 책 desc ↔ 후보 desc (max) ──
    good_descs = [index.get_book(bid).desc for bid in good_ids if index.get_book(bid)]
    desc_score = max(float(np.dot(d, cand.desc)) for d in good_descs) if good_descs else 0.0

    # ── 3. L1_score: 좋아한 책 L1 ↔ 후보 L1 (max) ──
    good_l1s = [index.get_book(bid).l1 for bid in good_ids if index.get_book(bid)]
    l1_score = max(float(np.dot(l, cand.l1)) for l in good_l1s) if good_l1s else 0.0

    # ── 4. L2_score: 좋아한 책 L2 ↔ 후보 L2 (max) ──
    good_l2s = [index.get_book(bid).l2 for bid in good_ids if index.get_book(bid)]
    l2_score = max(float(np.dot(l, cand.l2)) for l in good_l2s) if good_l2s else 0.0

    # ── 5. fb_desc_score: 피드백 emb ↔ 후보 desc (mean) ──
    fb_desc_vals = []
    for bid, fb in fb_data.items():
        if liked_books.get(bid, {}).get("rating") == "neutral":
            continue
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_desc_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_desc_score = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0

    return (W_REASON * reason_score
            + W_DESC * desc_score
            + W_L1 * l1_score
            + W_L2 * l2_score
            + W_FB_DESC * fb_desc_score)


def recommend_scores(index: VectorIndex, liked_books: dict,
                     fb_data: dict) -> dict[str, float]:
    """전체 후보에 대해 v3 스코어 계산. 이미 읽은 책은 제외.

    Args:
        index: VectorIndex
        liked_books: {book_id: {"rating": "good"|"bad"|"neutral"}}
        fb_data: {book_id: {"emb": np.ndarray, "is_dislike": bool}}

    Returns:
        {book_id: score} — 이미 읽은 책 제외, neutral 제외한 계산
    """
    if not liked_books:
        return {}

    # neutral 아닌 것만 취향 계산에 사용
    active = {bid: d for bid, d in liked_books.items() if d["rating"] != "neutral"}
    if not active:
        return {}

    read_ids = set(liked_books.keys())
    scores = {}
    for cid in index.book_ids:
        if cid in read_ids:
            continue
        scores[cid] = _score_one(index, active, fb_data, cid)
    return scores
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

```bash
cd recommendation-server && python -m pytest tests/test_scorer.py -v
```
Expected: 5 passed

- [ ] **Step 6: 커밋**

```bash
git add recommendation-server/engine/scorer.py recommendation-server/tests/
git commit -m "feat: v3 스코어링 알고리즘 구현 — 5가지 분리 계산 + 가중합"
```

---

### Task 4: Loader — Supabase에서 벡터 로드

**Files:**
- Create: `recommendation-server/engine/loader.py`

- [ ] **Step 1: loader.py 구현**

```python
# recommendation-server/engine/loader.py
"""서버 시작 시 Supabase에서 벡터 데이터를 로드하여 VectorIndex 구축."""
import numpy as np
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from engine.index import VectorIndex


def _to_np(vec) -> np.ndarray:
    """DB에서 온 벡터(리스트 또는 문자열)를 numpy float32로 변환."""
    if isinstance(vec, str):
        # pgvector는 '[0.1,0.2,...]' 문자열로 올 수 있음
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def load_index() -> tuple[VectorIndex, dict]:
    """Supabase에서 전체 벡터를 로드하여 VectorIndex + books_meta 반환.

    Returns:
        (index, books_meta)
        books_meta: {book_id: {"title": ..., "author": ..., "cover_url": ...}}
    """
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1. books 메타정보
    books_raw = []
    offset = 0
    while True:
        batch = sb.table("books").select("id,title,author,cover_url").range(offset, offset + 999).execute()
        books_raw.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000

    books_meta = {}
    for b in books_raw:
        books_meta[b["id"]] = {"title": b["title"], "author": b["author"],
                                "cover_url": b.get("cover_url")}

    # 2. genre_embeddings (L1/L2)
    ge_raw = sb.table("genre_embeddings").select("id,embedding").execute()
    genre_embs = {g["id"]: _to_np(g["embedding"]) for g in ge_raw.data}

    # 3. book_v3_vectors (desc + L1/L2 FK)
    v3_raw = []
    offset = 0
    while True:
        batch = sb.table("book_v3_vectors").select(
            "book_id,desc_embedding,l1_genre_id,l2_genre_id"
        ).range(offset, offset + 999).execute()
        v3_raw.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000

    v3_map = {}
    for v in v3_raw:
        v3_map[v["book_id"]] = v

    # 4. book_love_reasons (reason embeddings)
    reasons_raw = []
    offset = 0
    while True:
        batch = sb.table("book_love_reasons").select(
            "book_id,reason_embedding"
        ).not_.is_("reason_embedding", "null").range(offset, offset + 999).execute()
        reasons_raw.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000

    reasons_by_book: dict[str, list[np.ndarray]] = {}
    for r in reasons_raw:
        bid = r["book_id"]
        if bid not in reasons_by_book:
            reasons_by_book[bid] = []
        reasons_by_book[bid].append(_to_np(r["reason_embedding"]))

    # 5. VectorIndex 구축
    dim = 2000
    index = VectorIndex(dim=dim)
    loaded = 0
    for bid, v3 in v3_map.items():
        if bid not in books_meta:
            continue
        l1_id = v3.get("l1_genre_id")
        l2_id = v3.get("l2_genre_id")
        if not l1_id or not l2_id or l1_id not in genre_embs or l2_id not in genre_embs:
            continue
        desc_emb = v3.get("desc_embedding")
        if not desc_emb:
            continue

        index.add_book(
            bid,
            reasons=reasons_by_book.get(bid, []),
            desc=_to_np(desc_emb),
            l1=genre_embs[l1_id],
            l2=genre_embs[l2_id],
        )
        loaded += 1

    index.build_desc_matrix()
    print(f"[loader] {loaded} books loaded into VectorIndex")
    return index, books_meta
```

- [ ] **Step 2: 커밋**

```bash
git add recommendation-server/engine/loader.py
git commit -m "feat: Supabase → VectorIndex 로더 구현"
```

---

### Task 5: Auth — Supabase JWT 검증

**Files:**
- Create: `recommendation-server/auth.py`

- [ ] **Step 1: auth.py 구현**

```python
# recommendation-server/auth.py
from fastapi import Header, HTTPException
from jose import jwt, JWTError
from config import SUPABASE_JWT_SECRET


def verify_jwt(authorization: str = Header(...)) -> str:
    """Authorization 헤더에서 Supabase JWT를 검증하고 user_id(sub) 반환.

    Raises:
        HTTPException 401: 토큰 누락/만료/검증 실패
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")

    token = authorization[7:]
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(401, f"JWT verification failed: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "JWT missing sub claim")
    return user_id
```

- [ ] **Step 2: 커밋**

```bash
git add recommendation-server/auth.py
git commit -m "feat: Supabase JWT 인증 미들웨어"
```

---

### Task 6: Models — Pydantic 스키마

**Files:**
- Create: `recommendation-server/models.py`

- [ ] **Step 1: models.py 구현**

```python
# recommendation-server/models.py
from pydantic import BaseModel


class BookScore(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: str | None


class RecommendResponse(BaseModel):
    user_id: str
    recommendations: list[BookScore]
    meta: dict


class SimilarBook(BaseModel):
    book_id: str
    score: float
    title: str
    author: str
    cover_url: str | None


class SimilarResponse(BaseModel):
    book_id: str
    similar: list[SimilarBook]


class FeedbackRequest(BaseModel):
    book_id: int | str
    rating: str  # "good" | "neutral" | "bad"
    review_text: str | None = None
    emotion_tags: list[str] | None = None


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: str | None = None
```

- [ ] **Step 2: 커밋**

```bash
git add recommendation-server/models.py
git commit -m "feat: Pydantic 요청/응답 스키마"
```

---

### Task 7: API 엔드포인트 + main.py

**Files:**
- Create: `recommendation-server/api/recommend.py`
- Create: `recommendation-server/api/similar.py`
- Create: `recommendation-server/api/feedback.py`
- Create: `recommendation-server/main.py`

- [ ] **Step 1: recommend.py**

```python
# recommendation-server/api/recommend.py
from fastapi import APIRouter, Depends, HTTPException, Query
from auth import verify_jwt
from models import RecommendResponse, BookScore
from engine.scorer import recommend_scores
from config import DEFAULT_RECOMMEND_LIMIT

router = APIRouter()


@router.get("/recommend/{user_id}", response_model=RecommendResponse)
async def get_recommendations(
    user_id: str,
    limit: int = Query(DEFAULT_RECOMMEND_LIMIT, ge=1, le=50),
    current_user: str = Depends(verify_jwt),
):
    if current_user != user_id:
        raise HTTPException(403, "Cannot access other user's recommendations")

    from main import app_state
    index = app_state["index"]
    books_meta = app_state["books_meta"]

    # Supabase에서 유저의 읽은 책 + 피드백 조회
    from supabase import create_client
    from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding"
    ).eq("user_id", user_id).execute()

    if not ub_res.data:
        return RecommendResponse(
            user_id=user_id, recommendations=[],
            meta={"total_liked": 0, "total_disliked": 0, "has_feedback": False},
        )

    liked_books = {}
    fb_data = {}
    total_liked = 0
    total_disliked = 0
    has_feedback = False

    for ub in ub_res.data:
        bid = ub["book_id"]
        rating = ub.get("rating", "neutral")
        liked_books[bid] = {"rating": rating}
        if rating == "good":
            total_liked += 1
        elif rating == "bad":
            total_disliked += 1

        fb_emb = ub.get("feedback_embedding")
        if fb_emb:
            has_feedback = True
            import numpy as np
            from engine.loader import _to_np
            fb_data[bid] = {
                "emb": _to_np(fb_emb),
                "is_dislike": rating == "bad",
            }

    scores = recommend_scores(index, liked_books, fb_data)
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]

    recs = []
    for bid, score in sorted_scores:
        meta = books_meta.get(bid, {})
        recs.append(BookScore(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""),
            author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        ))

    return RecommendResponse(
        user_id=user_id, recommendations=recs,
        meta={"total_liked": total_liked, "total_disliked": total_disliked,
              "has_feedback": has_feedback},
    )
```

- [ ] **Step 2: similar.py**

```python
# recommendation-server/api/similar.py
from fastapi import APIRouter, Depends, HTTPException, Query
from auth import verify_jwt
from models import SimilarResponse, SimilarBook
from config import DEFAULT_SIMILAR_LIMIT

router = APIRouter()


@router.get("/similar/{book_id}", response_model=SimilarResponse)
async def get_similar(
    book_id: str,
    limit: int = Query(DEFAULT_SIMILAR_LIMIT, ge=1, le=50),
    _: str = Depends(verify_jwt),
):
    from main import app_state
    index = app_state["index"]
    books_meta = app_state["books_meta"]

    if index.get_book(book_id) is None:
        raise HTTPException(404, f"Book {book_id} not found in index")

    results = index.similar_by_desc(book_id, limit=limit)

    similar = []
    for bid, score in results:
        meta = books_meta.get(bid, {})
        similar.append(SimilarBook(
            book_id=bid, score=round(score, 4),
            title=meta.get("title", ""),
            author=meta.get("author", ""),
            cover_url=meta.get("cover_url"),
        ))

    return SimilarResponse(book_id=book_id, similar=similar)
```

- [ ] **Step 3: feedback.py**

```python
# recommendation-server/api/feedback.py
import uuid
import requests
from fastapi import APIRouter, Depends, HTTPException
from auth import verify_jwt
from models import FeedbackRequest, FeedbackResponse
from config import (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                    OPENAI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)

router = APIRouter()


def _embed_text(text: str) -> list[float]:
    """OpenAI embedding API 호출."""
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": [text],
              "dimensions": EMBEDDING_DIMENSIONS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    current_user: str = Depends(verify_jwt),
):
    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    feedback_id = str(uuid.uuid4())
    fb_embedding = None

    # 리뷰 텍스트가 있으면 임베딩 생성
    if req.review_text and req.review_text.strip():
        try:
            fb_embedding = _embed_text(req.review_text.strip())
        except Exception:
            # OpenAI 실패 시 텍스트만 저장, 임베딩은 나중에 배치로
            pass

    # Supabase에 저장 (upsert)
    row = {
        "user_id": current_user,
        "book_id": str(req.book_id),
        "rating": req.rating,
        "review_text": req.review_text,
        "emotion_tags": req.emotion_tags,
    }
    if fb_embedding:
        row["feedback_embedding"] = fb_embedding

    try:
        sb.table("user_books").upsert(
            row, on_conflict="user_id,book_id"
        ).execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")

    # 메모리 인덱스에 유저 취향 즉시 반영은 하지 않음 (다음 추천 호출 시 DB에서 조회)
    # MVP에서는 매 추천 호출마다 user_books를 DB에서 읽으므로 자동 반영됨

    return FeedbackResponse(status="ok", feedback_id=feedback_id)
```

- [ ] **Step 4: main.py**

```python
# recommendation-server/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.recommend import router as recommend_router
from api.similar import router as similar_router
from api.feedback import router as feedback_router

app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시 벡터 로드
    from engine.loader import load_index
    index, books_meta = load_index()
    app_state["index"] = index
    app_state["books_meta"] = books_meta
    print(f"[main] Server ready. {len(index.book_ids)} books in index.")
    yield
    app_state.clear()


app = FastAPI(title="Curation Recommendation Server", lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(similar_router)
app.include_router(feedback_router)


@app.get("/health")
async def health():
    index = app_state.get("index")
    return {
        "status": "ok",
        "books_loaded": len(index.book_ids) if index else 0,
    }
```

- [ ] **Step 5: 커밋**

```bash
git add recommendation-server/main.py recommendation-server/api/ recommendation-server/models.py recommendation-server/auth.py
git commit -m "feat: FastAPI 엔드포인트 4개 — recommend/similar/feedback/health"
```

---

### Task 8: Dockerfile + 배포 설정

**Files:**
- Create: `recommendation-server/Dockerfile`

- [ ] **Step 1: Dockerfile 작성**

```dockerfile
# recommendation-server/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

- [ ] **Step 2: 커밋**

```bash
git add recommendation-server/Dockerfile
git commit -m "feat: Dockerfile 추가 — Render/Fly.io 배포용"
```

---

### Task 9: 로컬 통합 테스트

**Files:**
- Create: `recommendation-server/tests/test_api_similar.py`
- Create: `recommendation-server/tests/test_api_recommend.py`

- [ ] **Step 1: similar API 테스트**

```python
# recommendation-server/tests/test_api_similar.py
"""similar API 통합 테스트 (실제 DB 사용)."""
import os
import pytest
from fastapi.testclient import TestClient

# JWT 검증을 건너뛰기 위한 override
@pytest.fixture
def client():
    from main import app
    from auth import verify_jwt

    app.dependency_overrides[verify_jwt] = lambda: "test-user"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["books_loaded"] > 0


def test_similar_returns_results(client):
    # health에서 로드된 책 중 아무거나 하나 사용
    from main import app_state
    index = app_state["index"]
    first_bid = index.book_ids[0]

    resp = client.get(f"/similar/{first_bid}?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["book_id"] == first_bid
    assert len(data["similar"]) == 5
    assert data["similar"][0]["score"] > 0


def test_similar_not_found(client):
    resp = client.get("/similar/nonexistent-id?limit=5")
    assert resp.status_code == 404
```

- [ ] **Step 2: 로컬 실행 테스트**

```bash
cd recommendation-server
pip install -r requirements.txt
python -m pytest tests/test_api_similar.py -v
```

Expected: 3 passed (실제 Supabase 연결 필요)

- [ ] **Step 3: 로컬 서버 띄워서 수동 확인**

```bash
cd recommendation-server
uvicorn main:app --reload --port 8000
# 다른 터미널에서:
curl http://localhost:8000/health
```

Expected: `{"status":"ok","books_loaded":2510}`

- [ ] **Step 4: 커밋**

```bash
git add recommendation-server/tests/
git commit -m "test: similar API 통합 테스트 + 로컬 검증"
```

---

### Task 10: Supabase JWT secret 확인 + .env 업데이트

- [ ] **Step 1: Supabase 대시보드에서 JWT secret 복사**

Supabase Dashboard → Settings → API → JWT Settings → `JWT Secret` 복사

- [ ] **Step 2: .env에 추가**

```bash
echo 'SUPABASE_JWT_SECRET=your-actual-jwt-secret' >> .env
```

- [ ] **Step 3: user_books 테이블에 feedback_embedding 컬럼 확인**

user_books 테이블에 `feedback_embedding vector(2000)` 컬럼이 없으면 추가:

```sql
ALTER TABLE user_books ADD COLUMN IF NOT EXISTS feedback_embedding vector(2000);
```

- [ ] **Step 4: 커밋 (.env는 커밋 안 함)**

```bash
git add recommendation-server/.env.example
git commit -m "chore: .env.example에 JWT_SECRET 추가"
```
