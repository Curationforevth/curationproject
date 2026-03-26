# 추천 엔진 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 유저 피드백 기반 도서 추천 엔진을 MVP부터 작동하도록 구현. book-to-book 유사도 + 취향 벡터 기반 추천 + 신뢰도 스코어링 + 단계적 진화(가중 평균→K-means) 전체 인프라 셋업.

**Architecture:** Supabase RPC로 실시간 유사도 검색 + 즉시 취향 벡터 재계산. GitHub Actions Python 배치로 K-means 클러스터링 + LLM 요약. 두 경로가 같은 user_taste_vectors 테이블에 쓰며, 배치가 즉시 계산을 자연스럽게 덮어쓰는 구조.

**Tech Stack:** PostgreSQL (pgvector), Supabase RPC, Python (sklearn, OpenAI), GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-26-recommendation-engine-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `supabase/009_recommendation.sql` | Create | DB 마이그레이션 (컬럼 추가 + RPC 함수 4개) |
| `scripts/taste_recomputer.py` | Create | 배치 취향 벡터 재계산 (가중 평균 + K-means + LLM 요약) |
| `scripts/tests/test_taste_recomputer.py` | Create | 순수 함수 테스트 |
| `.github/workflows/daily-taste-recompute.yml` | Create | 배치 워크플로우 |
| `docs/ARCHITECTURE.md` | Modify | 추천 엔진 섹션 동기화 |

---

### Task 1: DB 마이그레이션

**Files:**
- Create: `supabase/009_recommendation.sql`

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- supabase/009_recommendation.sql
-- =============================================
-- 009: 추천 엔진 인프라
-- Spec: docs/superpowers/specs/2026-03-26-recommendation-engine-design.md
-- =============================================

-- 1. user_taste_vectors 컬럼 추가
ALTER TABLE public.user_taste_vectors ADD COLUMN IF NOT EXISTS weight float DEFAULT 1.0;
ALTER TABLE public.user_taste_vectors ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE public.user_taste_vectors ADD COLUMN IF NOT EXISTS method text DEFAULT 'weighted_avg';

-- 2. users 테이블에 추천 신뢰도 캐싱
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS recommendation_confidence jsonb;

-- 3. RPC: book-to-book 유사도
CREATE OR REPLACE FUNCTION public.match_books_by_similarity(
  target_book_id uuid,
  match_count int DEFAULT 10
)
RETURNS TABLE(book_id uuid, similarity float) AS $$
BEGIN
  RETURN QUERY
  SELECT
    be2.book_id,
    1 - (be1.embedding <=> be2.embedding) AS similarity
  FROM book_embeddings be1
  CROSS JOIN LATERAL (
    SELECT be.book_id, be.embedding
    FROM book_embeddings be
    WHERE be.book_id != target_book_id
    ORDER BY be.embedding <=> be1.embedding
    LIMIT match_count
  ) be2
  WHERE be1.book_id = target_book_id;
END;
$$ LANGUAGE plpgsql STABLE;

-- 4. RPC: taste-to-book 추천
CREATE OR REPLACE FUNCTION public.recommend_books_for_user(
  target_user_id uuid,
  match_count int DEFAULT 10
)
RETURNS TABLE(book_id uuid, similarity float, cluster_label text) AS $$
BEGIN
  RETURN QUERY
  WITH user_read_books AS (
    SELECT ub.book_id FROM user_books ub WHERE ub.user_id = target_user_id
  ),
  user_bad_books AS (
    SELECT ub.book_id FROM user_books ub
    WHERE ub.user_id = target_user_id AND ub.rating = 'bad'
  ),
  bad_embeddings AS (
    SELECT be.embedding FROM book_embeddings be
    JOIN user_bad_books ubb ON ubb.book_id = be.book_id
  ),
  taste_matches AS (
    SELECT
      be.book_id,
      utv.weight * (1 - (utv.vector <=> be.embedding)) AS weighted_similarity,
      utv.cluster_label
    FROM user_taste_vectors utv
    CROSS JOIN LATERAL (
      SELECT be2.book_id, be2.embedding
      FROM book_embeddings be2
      WHERE be2.book_id NOT IN (SELECT urb.book_id FROM user_read_books urb)
      ORDER BY be2.embedding <=> utv.vector
      LIMIT match_count * 2
    ) be
    WHERE utv.user_id = target_user_id
  )
  SELECT
    tm.book_id,
    MAX(tm.weighted_similarity) AS similarity,
    (ARRAY_AGG(tm.cluster_label ORDER BY tm.weighted_similarity DESC))[1] AS cluster_label
  FROM taste_matches tm
  WHERE NOT EXISTS (
    SELECT 1 FROM bad_embeddings bad
    WHERE 1 - (bad.embedding <=> (SELECT embedding FROM book_embeddings WHERE book_embeddings.book_id = tm.book_id)) > 0.85
  )
  GROUP BY tm.book_id
  ORDER BY MAX(tm.weighted_similarity) DESC
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;

-- 5. RPC: 추천 신뢰도 스코어
CREATE OR REPLACE FUNCTION public.calculate_recommendation_confidence(
  target_user_id uuid
)
RETURNS jsonb AS $$
DECLARE
  result jsonb;
  total_depth float := 0;
  book_count int := 0;
  unique_genres int := 0;
  genre_cap int := 4;
  rating_values text[];
  rating_var float := 0;
  diversity float := 0;
  score float := 0;
BEGIN
  -- 피드백 깊이 합산 + 권수
  SELECT
    COUNT(*)::int,
    COALESCE(SUM(
      CASE
        WHEN review_text IS NOT NULL AND LENGTH(review_text) >= 50 THEN 5
        WHEN emotion_tags IS NOT NULL AND jsonb_array_length(emotion_tags) >= 3 THEN 4
        WHEN emotion_tags IS NOT NULL AND jsonb_array_length(emotion_tags) >= 1 THEN 3
        WHEN rating IS NOT NULL THEN 2
        ELSE 1
      END
    ), 0)
  INTO book_count, total_depth
  FROM user_books
  WHERE user_id = target_user_id AND status = 'read';

  -- 장르 다양성
  SELECT COUNT(DISTINCT b.genre)::int
  INTO unique_genres
  FROM user_books ub
  JOIN books b ON b.id = ub.book_id
  WHERE ub.user_id = target_user_id AND ub.status = 'read' AND b.genre IS NOT NULL;

  IF book_count >= 3 THEN
    diversity := LEAST(unique_genres::float / genre_cap, 1.0);
  ELSE
    diversity := 0;
  END IF;

  -- 호오 분산
  SELECT ARRAY_AGG(DISTINCT rating)
  INTO rating_values
  FROM user_books
  WHERE user_id = target_user_id AND rating IS NOT NULL;

  IF rating_values IS NOT NULL THEN
    rating_var := ARRAY_LENGTH(rating_values, 1)::float / 3.0;
  END IF;

  -- 종합 스코어 (가중 합산, 0~1 범위로 정규화)
  score := LEAST(
    (total_depth / 25.0) * 0.4 +
    diversity * 0.3 +
    rating_var * 0.15 +
    LEAST(book_count::float / 10.0, 1.0) * 0.15,
    1.0
  );

  result := jsonb_build_object(
    'score', ROUND(score::numeric, 3),
    'feedback_depth', total_depth,
    'book_count', book_count,
    'genre_diversity', ROUND(diversity::numeric, 3),
    'rating_variance', ROUND(rating_var::numeric, 3),
    'updated_at', NOW()
  );

  -- 캐싱
  UPDATE users SET recommendation_confidence = result WHERE id = target_user_id;

  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- 6. RPC: 즉시 취향 벡터 재계산 (가중 평균)
CREATE OR REPLACE FUNCTION public.recompute_taste_vector_immediate(
  target_user_id uuid
)
RETURNS void AS $$
DECLARE
  current_method text;
  avg_vector vector(1536);
BEGIN
  -- method 확인
  SELECT method INTO current_method
  FROM user_taste_vectors
  WHERE user_id = target_user_id
  LIMIT 1;

  -- kmeans 유저는 즉시 경로에서 전체 재계산 안 함 (배치에서 처리)
  -- 대신 confidence만 갱신
  IF current_method = 'kmeans' THEN
    PERFORM calculate_recommendation_confidence(target_user_id);
    RETURN;
  END IF;

  -- 가중 평균 계산
  SELECT
    AVG(be.embedding *
      CASE
        WHEN ub.review_text IS NOT NULL AND LENGTH(ub.review_text) >= 50 THEN 3.0
        WHEN ub.emotion_tags IS NOT NULL AND jsonb_array_length(ub.emotion_tags) >= 1 THEN 2.0
        WHEN ub.rating IS NOT NULL THEN 1.5
        ELSE 1.0
      END
    )
  INTO avg_vector
  FROM user_books ub
  JOIN book_embeddings be ON be.book_id = ub.book_id
  WHERE ub.user_id = target_user_id
    AND ub.status = 'read'
    AND ub.rating IS DISTINCT FROM 'bad';

  IF avg_vector IS NOT NULL THEN
    INSERT INTO user_taste_vectors (user_id, cluster_label, vector, weight, method)
    VALUES (target_user_id, NULL, avg_vector, 1.0, 'weighted_avg')
    ON CONFLICT (user_id, cluster_label)
      WHERE cluster_label IS NULL
    DO UPDATE SET
      vector = EXCLUDED.vector,
      weight = EXCLUDED.weight,
      method = EXCLUDED.method,
      updated_at = NOW();
  END IF;

  -- confidence 갱신
  PERFORM calculate_recommendation_confidence(target_user_id);
END;
$$ LANGUAGE plpgsql;

-- 7. user_taste_vectors에 unique constraint 추가 (upsert용)
ALTER TABLE public.user_taste_vectors
  ADD CONSTRAINT uq_user_taste_vectors_user_label
  UNIQUE (user_id, cluster_label);

-- 8. user_taste_vectors에 HNSW 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_user_taste_vectors_hnsw
  ON public.user_taste_vectors
  USING hnsw (vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

- [ ] **Step 2: Supabase Dashboard SQL Editor에서 실행**

- [ ] **Step 3: 커밋**

```bash
git add supabase/009_recommendation.sql
git commit -m "feat: 추천 엔진 DB 마이그레이션 — RPC 함수 4개 + 컬럼 추가 + 인덱스"
```

---

### Task 2: taste_recomputer 순수 함수 + 테스트

**Files:**
- Create: `scripts/tests/test_taste_recomputer.py`
- Create: `scripts/taste_recomputer.py` (순수 함수 부분만)

- [ ] **Step 1: 테스트 파일 작성**

```python
# scripts/tests/test_taste_recomputer.py
"""취향 재계산기 순수 함수 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestFeedbackDepthScore:
    """피드백 깊이 스코어 계산"""

    def test_read_only(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": None, "emotion_tags": None, "review_text": None}
        assert feedback_depth_score(book) == 1

    def test_with_rating(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": None, "review_text": None}
        assert feedback_depth_score(book) == 2

    def test_with_few_tags(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한", "따뜻한"], "review_text": None}
        assert feedback_depth_score(book) == 3

    def test_with_many_tags(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한", "따뜻한", "몰입"], "review_text": None}
        assert feedback_depth_score(book) == 4

    def test_with_review(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": "a" * 50}
        assert feedback_depth_score(book) == 5

    def test_short_review_not_counted(self):
        from taste_recomputer import feedback_depth_score
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": "짧은 리뷰"}
        assert feedback_depth_score(book) == 3


class TestShouldUpgradeToKmeans:
    """클러스터링 전환 판단"""

    def test_too_few_books(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(5, 'weighted_avg') == False

    def test_enough_books_weighted_avg(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(12, 'weighted_avg') == True

    def test_already_kmeans(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(12, 'kmeans') == True

    def test_boundary(self):
        from taste_recomputer import should_upgrade_to_kmeans
        assert should_upgrade_to_kmeans(10, 'weighted_avg') == True


class TestFeedbackWeight:
    """가중 평균용 weight 계산"""

    def test_read_only(self):
        from taste_recomputer import feedback_weight
        book = {"rating": None, "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 1.0

    def test_with_rating_good(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 1.5

    def test_with_tags(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": ["잔잔한"], "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 2.0

    def test_with_review(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": ["잔잔한"],
                "review_text": "a" * 50, "is_onboarding_favorite": False}
        assert feedback_weight(book) == 3.0

    def test_favorite_bonus(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "good", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": True}
        assert feedback_weight(book) == 1.5 * 1.2

    def test_bad_rating_excluded(self):
        from taste_recomputer import feedback_weight
        book = {"rating": "bad", "emotion_tags": None, "review_text": None,
                "is_onboarding_favorite": False}
        assert feedback_weight(book) == 0
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd scripts && python3 -m pytest tests/test_taste_recomputer.py -v
```
Expected: FAIL (모듈 미존재)

- [ ] **Step 3: 순수 함수 구현**

```python
# scripts/taste_recomputer.py
"""
취향 벡터 재계산기 — 배치 경로

가중 평균 → K-means 클러스터링 단계적 진화.
즉시 경로(RPC)가 처리하는 가중 평균을 배치에서 더 정교한 클러스터링으로 덮어쓴다.

사용법:
  python3 scripts/taste_recomputer.py                # 전체 유저 재계산
  python3 scripts/taste_recomputer.py --limit 10     # 10명만
  python3 scripts/taste_recomputer.py --user-id UUID # 특정 유저
  python3 scripts/taste_recomputer.py --status        # 현황
  python3 scripts/taste_recomputer.py --dry-run       # DB 저장 없이

의존성:
  pip install supabase python-dotenv scikit-learn numpy
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
except ImportError:
    pass

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()


KMEANS_MIN_BOOKS = 10
KMEANS_MAX_K = 5
SILHOUETTE_THRESHOLD = 0.2
FAVORITE_BONUS = 1.2
MIN_REVIEW_LENGTH = 50


# --- 순수 함수 ---

def feedback_depth_score(book):
    """책 1권의 피드백 깊이 스코어 (1~5)."""
    review = book.get("review_text") or ""
    tags = book.get("emotion_tags") or []
    rating = book.get("rating")

    if review and len(review) >= MIN_REVIEW_LENGTH:
        return 5
    if tags and len(tags) >= 3:
        return 4
    if tags and len(tags) >= 1:
        return 3
    if rating:
        return 2
    return 1


def feedback_weight(book):
    """가중 평균용 weight. bad 평가는 0 (제외)."""
    if book.get("rating") == "bad":
        return 0

    review = book.get("review_text") or ""
    tags = book.get("emotion_tags") or []
    rating = book.get("rating")

    w = 1.0
    if review and len(review) >= MIN_REVIEW_LENGTH:
        w = 3.0
    elif tags and len(tags) >= 1:
        w = 2.0
    elif rating:
        w = 1.5

    if book.get("is_onboarding_favorite"):
        w *= FAVORITE_BONUS

    return w


def should_upgrade_to_kmeans(book_count, current_method):
    """클러스터링 전환 여부 판단."""
    if book_count >= KMEANS_MIN_BOOKS:
        return True
    return False
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
cd scripts && python3 -m pytest tests/test_taste_recomputer.py -v
```
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/taste_recomputer.py scripts/tests/test_taste_recomputer.py
git commit -m "feat: 취향 재계산기 순수 함수 + 테스트 — depth score, weight, kmeans 판단"
```

---

### Task 3: taste_recomputer 클래스 + CLI

**Files:**
- Modify: `scripts/taste_recomputer.py` (클래스 + CLI 추가)

- [ ] **Step 1: TasteRecomputer 클래스 추가**

`scripts/taste_recomputer.py`의 순수 함수 아래에 클래스 추가:

```python
# --- 재계산 클래스 ---

class TasteRecomputer:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self.stats = {
            "processed": 0, "weighted_avg": 0,
            "kmeans": 0, "skipped": 0, "errors": 0,
        }

    def fetch_users_with_books(self, limit=0, user_id=None):
        """피드백이 있는 유저 목록 조회."""
        if user_id:
            result = with_retry(lambda: (
                self.sb.table("user_books")
                .select("user_id")
                .eq("user_id", user_id)
                .eq("status", "read")
                .limit(1)
                .execute()
            ))
            return [user_id] if result.data else []

        # 읽은 책이 있는 유저 목록
        all_users = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("user_books")
                .select("user_id")
                .eq("status", "read")
                .range(o, o + page_size - 1)
                .execute()
            ))
            if not result.data:
                break
            for row in result.data:
                all_users.add(row["user_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        users = list(all_users)
        if limit > 0:
            users = users[:limit]
        return users

    def fetch_user_books_with_embeddings(self, user_id):
        """유저의 읽은 책 + 임베딩 조회."""
        result = with_retry(lambda: (
            self.sb.rpc("get_user_books_with_embeddings", {"target_user_id": user_id})
            .execute()
        ))
        # RPC가 없으면 직접 조회
        if not result.data:
            books_result = with_retry(lambda: (
                self.sb.table("user_books")
                .select("book_id, rating, emotion_tags, review_text")
                .eq("user_id", user_id)
                .eq("status", "read")
                .execute()
            ))
            if not books_result.data:
                return []

            book_ids = [b["book_id"] for b in books_result.data]
            embeddings_result = with_retry(lambda: (
                self.sb.table("book_embeddings")
                .select("book_id, embedding")
                .in_("book_id", book_ids)
                .execute()
            ))
            emb_map = {e["book_id"]: e["embedding"] for e in (embeddings_result.data or [])}

            combined = []
            for b in books_result.data:
                if b["book_id"] in emb_map:
                    b["embedding"] = emb_map[b["book_id"]]
                    combined.append(b)
            return combined
        return result.data

    def compute_weighted_average(self, books):
        """가중 평균 취향 벡터 계산."""
        vectors = []
        weights = []
        for book in books:
            w = feedback_weight(book)
            if w <= 0:
                continue
            vectors.append(np.array(book["embedding"]))
            weights.append(w)

        if not vectors:
            return None

        vectors = np.array(vectors)
        weights = np.array(weights)
        avg = np.average(vectors, axis=0, weights=weights)
        return avg.tolist()

    def compute_kmeans(self, books):
        """K-means 클러스터링. 최적 k 자동 탐색."""
        vectors = []
        weights = []
        for book in books:
            w = feedback_weight(book)
            if w <= 0:
                continue
            vectors.append(np.array(book["embedding"]))
            weights.append(w)

        if len(vectors) < KMEANS_MIN_BOOKS:
            return None

        X = np.array(vectors)
        W = np.array(weights)

        best_k = 1
        best_score = -1
        best_labels = None

        max_k = min(KMEANS_MAX_K, len(vectors) - 1)
        for k in range(2, max_k + 1):
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(X)
            score = silhouette_score(X, labels)
            if score > best_score:
                best_score = score
                best_k = k
                best_labels = labels

        if best_score < SILHOUETTE_THRESHOLD:
            return None

        # 클러스터별 가중 평균 centroid + weight
        clusters = []
        for c in range(best_k):
            mask = best_labels == c
            cluster_vectors = X[mask]
            cluster_weights = W[mask]
            centroid = np.average(cluster_vectors, axis=0, weights=cluster_weights)
            cluster_weight = float(cluster_weights.sum()) / float(W.sum())
            clusters.append({
                "vector": centroid.tolist(),
                "weight": round(cluster_weight, 3),
                "book_count": int(mask.sum()),
            })

        return clusters

    def save_taste_vectors(self, user_id, method, vectors_data):
        """user_taste_vectors에 저장."""
        if self.dry_run:
            return

        # 기존 벡터 삭제
        with_retry(lambda: (
            self.sb.table("user_taste_vectors")
            .delete()
            .eq("user_id", user_id)
            .execute()
        ))

        if method == "weighted_avg":
            row = {
                "user_id": user_id,
                "cluster_label": None,
                "vector": vectors_data,
                "weight": 1.0,
                "method": "weighted_avg",
            }
            with_retry(lambda: (
                self.sb.table("user_taste_vectors")
                .insert(row)
                .execute()
            ))
        elif method == "kmeans":
            rows = []
            for i, cluster in enumerate(vectors_data):
                rows.append({
                    "user_id": user_id,
                    "cluster_label": f"cluster_{i}",
                    "vector": cluster["vector"],
                    "weight": cluster["weight"],
                    "method": "kmeans",
                })
            with_retry(lambda: (
                self.sb.table("user_taste_vectors")
                .insert(rows)
                .execute()
            ))

    def process_user(self, user_id):
        """단일 유저 처리."""
        books = self.fetch_user_books_with_embeddings(user_id)
        if not books:
            self.stats["skipped"] += 1
            return

        book_count = len([b for b in books if feedback_weight(b) > 0])

        # 현재 method 확인
        existing = with_retry(lambda: (
            self.sb.table("user_taste_vectors")
            .select("method")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ))
        current_method = existing.data[0]["method"] if existing.data else "weighted_avg"

        if should_upgrade_to_kmeans(book_count, current_method):
            clusters = self.compute_kmeans(books)
            if clusters:
                self.save_taste_vectors(user_id, "kmeans", clusters)
                self.stats["kmeans"] += 1
                self.stats["processed"] += 1
                return

        # 가중 평균 fallback
        avg = self.compute_weighted_average(books)
        if avg:
            self.save_taste_vectors(user_id, "weighted_avg", avg)
            self.stats["weighted_avg"] += 1
        else:
            self.stats["skipped"] += 1

        self.stats["processed"] += 1

    def run(self, limit=0, user_id=None):
        """메인 실행."""
        print(f"🔍 대상 유저 조회 중...")
        users = self.fetch_users_with_books(limit=limit, user_id=user_id)
        print(f"   {len(users)}명 발견\n")

        if not users:
            print("✅ 처리할 유저가 없습니다.")
            return

        for i, uid in enumerate(users):
            try:
                self.process_user(uid)
                if (i + 1) % 10 == 0 or (i + 1) <= 3:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{i + 1}/{len(users)}: "
                          f"avg={self.stats['weighted_avg']} km={self.stats['kmeans']}")
            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({uid[:8]}...): {e}")

        self._print_report(len(users))

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}취향 벡터 재계산 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}명")
        print(f"  처리 완료: {s['processed']}명")
        print(f"  가중 평균: {s['weighted_avg']}명")
        print(f"  K-means: {s['kmeans']}명")
        print(f"  스킵: {s['skipped']}명")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total_users = with_retry(lambda: self.sb.table("user_books")
                                  .select("user_id", count="exact").execute())
        has_taste = with_retry(lambda: self.sb.table("user_taste_vectors")
                                .select("user_id", count="exact").execute())
        has_kmeans = with_retry(lambda: self.sb.table("user_taste_vectors")
                                 .select("user_id", count="exact")
                                 .eq("method", "kmeans").execute())

        print(f"\n{'=' * 50}")
        print("취향 벡터 현황")
        print(f"{'=' * 50}")
        print(f"  책 등록 유저: {total_users.count}명")
        print(f"  취향 벡터 있음: {has_taste.count}명")
        print(f"  K-means 활성: {has_kmeans.count}명")
        print(f"  가중 평균: {has_taste.count - has_kmeans.count}명")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="취향 벡터 배치 재계산기")
    parser.add_argument("--limit", type=int, default=0, help="최대 유저 수 (0=전부)")
    parser.add_argument("--user-id", type=str, default=None, help="특정 유저 UUID")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="현황 조회")
    args = parser.parse_args()

    recomputer = TasteRecomputer(dry_run=args.dry_run)

    if args.status:
        recomputer.show_status()
        return

    recomputer.run(limit=args.limit, user_id=args.user_id)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 기존 테스트 통과 확인**

```bash
cd scripts && python3 -m pytest tests/test_taste_recomputer.py -v
```
Expected: 12 passed

- [ ] **Step 3: 전체 테스트 통과 확인**

```bash
cd scripts && python3 -m pytest tests/ -v
```
Expected: 전체 통과

- [ ] **Step 4: 커밋**

```bash
git add scripts/taste_recomputer.py
git commit -m "feat: 취향 재계산기 클래스 + CLI — 가중 평균/K-means 단계적 전환"
```

---

### Task 4: requirements.txt 업데이트

**Files:**
- Modify: `scripts/requirements.txt`

- [ ] **Step 1: scikit-learn, numpy 추가 확인**

```bash
cat scripts/requirements.txt
```

- [ ] **Step 2: 누락된 의존성 추가**

`scripts/requirements.txt`에 없으면 추가:
```
scikit-learn
numpy
```

- [ ] **Step 3: 커밋**

```bash
git add scripts/requirements.txt
git commit -m "chore: scikit-learn, numpy 의존성 추가 (취향 클러스터링용)"
```

---

### Task 5: 워크플로우 통합

**Files:**
- Create: `.github/workflows/daily-taste-recompute.yml`

- [ ] **Step 1: 워크플로우 작성**

```yaml
# .github/workflows/daily-taste-recompute.yml
name: Daily Taste Vector Recomputation

on:
  schedule:
    - cron: '0 22 * * *'  # UTC 22:00 = KST 07:00 (daily-embed-t2 이후)
  workflow_dispatch:

jobs:
  recompute-taste:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Recompute taste vectors
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/taste_recomputer.py

      - name: Show status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/taste_recomputer.py --status
```

- [ ] **Step 2: 커밋**

```bash
git add .github/workflows/daily-taste-recompute.yml
git commit -m "ci: 취향 벡터 배치 재계산 워크플로우 — KST 07:00 daily"
```

---

### Task 6: ARCHITECTURE.md 동기화

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: 데이터 흐름에 추천 경로 추가**

`docs/ARCHITECTURE.md`의 데이터 흐름 섹션 (daily-embed-t2 뒤)에 추가:

```
매일 KST 07:00 (daily-taste-recompute):
  → 전체 유저 취향 벡터 재계산 (가중 평균 또는 K-means 자동 전환)
  → 추천 신뢰도 스코어 갱신

실시간 (유저 피드백 제출 시):
  → Supabase RPC: recompute_taste_vector_immediate → 즉시 취향 벡터 갱신
  → Supabase RPC: match_books_by_similarity → book-to-book 유사도
  → Supabase RPC: recommend_books_for_user → 취향 기반 추천
```

- [ ] **Step 2: user_taste_vectors 테이블 스키마 업데이트**

```
| weight | float | 클러스터 크기 가중치 (기본 1.0) |
| summary | text | LLM 취향 요약 |
| method | text | 계산 방식 ('weighted_avg' 또는 'kmeans') |
```

- [ ] **Step 3: users 테이블에 recommendation_confidence 추가**

```
| recommendation_confidence | jsonb | 추천 신뢰도 캐싱 |
```

- [ ] **Step 4: 커밋**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: ARCHITECTURE.md 추천 엔진 파이프라인 + 스키마 동기화"
```

---

### Task 7: 통합 검증

**Files:** 없음 (운영 작업)

- [ ] **Step 1: RPC 함수 검증 — book-to-book**

Supabase Dashboard SQL Editor에서:
```sql
SELECT * FROM match_books_by_similarity(
  (SELECT id FROM books WHERE title LIKE '%불편한 편의점%' LIMIT 1),
  5
);
```
Expected: 5개 유사 책 + similarity 점수

- [ ] **Step 2: RPC 함수 검증 — confidence score**

테스트 유저가 있다면:
```sql
SELECT calculate_recommendation_confidence('유저-UUID');
```
Expected: jsonb with score, feedback_depth, book_count, genre_diversity, rating_variance

- [ ] **Step 3: taste_recomputer --status 확인**

```bash
python3 scripts/taste_recomputer.py --status
```
Expected: 현황 출력

- [ ] **Step 4: 전체 테스트 실행**

```bash
cd scripts && python3 -m pytest tests/ -v
```
Expected: 전체 통과 (기존 39 + 신규 12 = 51 tests)
