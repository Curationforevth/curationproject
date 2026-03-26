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
  bad_book_embeddings AS (
    SELECT be.embedding FROM book_embeddings be
    JOIN user_books ub ON ub.book_id = be.book_id
    WHERE ub.user_id = target_user_id AND ub.rating = 'bad'
  ),
  taste_matches AS (
    SELECT
      candidate.book_id,
      candidate.embedding AS candidate_embedding,
      utv.weight * (1 - (utv.vector <=> candidate.embedding)) AS weighted_similarity,
      utv.cluster_label
    FROM user_taste_vectors utv
    CROSS JOIN LATERAL (
      SELECT be2.book_id, be2.embedding
      FROM book_embeddings be2
      WHERE be2.book_id NOT IN (SELECT urb.book_id FROM user_read_books urb)
      ORDER BY be2.embedding <=> utv.vector
      LIMIT match_count * 2
    ) candidate
    WHERE utv.user_id = target_user_id
  )
  SELECT
    tm.book_id,
    MAX(tm.weighted_similarity) AS similarity,
    (ARRAY_AGG(tm.cluster_label ORDER BY tm.weighted_similarity DESC))[1] AS cluster_label
  FROM taste_matches tm
  WHERE NOT EXISTS (
    SELECT 1 FROM bad_book_embeddings bad
    WHERE 1 - (bad.embedding <=> tm.candidate_embedding) > 0.85
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

  SELECT ARRAY_AGG(DISTINCT rating)
  INTO rating_values
  FROM user_books
  WHERE user_id = target_user_id AND rating IS NOT NULL;

  IF rating_values IS NOT NULL THEN
    rating_var := ARRAY_LENGTH(rating_values, 1)::float / 3.0;
  END IF;

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

  UPDATE users SET recommendation_confidence = result WHERE id = target_user_id;

  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- 6. RPC: 즉시 취향 벡터 재계산 (진짜 가중 평균: SUM(v*w)/SUM(w))
CREATE OR REPLACE FUNCTION public.recompute_taste_vector_immediate(
  target_user_id uuid
)
RETURNS void AS $$
DECLARE
  current_method text;
  weighted_sum vector(1536);
  total_weight float;
  avg_vector vector(1536);
BEGIN
  SELECT method INTO current_method
  FROM user_taste_vectors
  WHERE user_id = target_user_id
  LIMIT 1;

  IF current_method = 'kmeans' THEN
    PERFORM calculate_recommendation_confidence(target_user_id);
    RETURN;
  END IF;

  -- 진짜 가중 평균: SUM(embedding * weight) / SUM(weight)
  SELECT
    SUM(be.embedding * w.feedback_w),
    SUM(w.feedback_w)
  INTO weighted_sum, total_weight
  FROM user_books ub
  JOIN book_embeddings be ON be.book_id = ub.book_id
  CROSS JOIN LATERAL (
    SELECT
      CASE
        WHEN ub.review_text IS NOT NULL AND LENGTH(ub.review_text) >= 50 THEN 3.0
        WHEN ub.emotion_tags IS NOT NULL AND jsonb_array_length(ub.emotion_tags) >= 1 THEN 2.0
        WHEN ub.rating IS NOT NULL THEN 1.5
        ELSE 1.0
      END AS feedback_w
  ) w
  WHERE ub.user_id = target_user_id
    AND ub.status = 'read'
    AND ub.rating IS DISTINCT FROM 'bad';

  IF weighted_sum IS NOT NULL AND total_weight > 0 THEN
    avg_vector := weighted_sum * (1.0 / total_weight);

    INSERT INTO user_taste_vectors (user_id, cluster_label, vector, weight, method)
    VALUES (target_user_id, NULL, avg_vector, 1.0, 'weighted_avg')
    ON CONFLICT ON CONSTRAINT uq_user_taste_vectors_user_null_label
    DO UPDATE SET
      vector = EXCLUDED.vector,
      weight = EXCLUDED.weight,
      method = EXCLUDED.method,
      updated_at = NOW();
  END IF;

  PERFORM calculate_recommendation_confidence(target_user_id);
END;
$$ LANGUAGE plpgsql;

-- 7. user_taste_vectors에 partial unique index 추가 (NULL cluster_label용 upsert)
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_taste_vectors_user_null_label
  ON public.user_taste_vectors (user_id)
  WHERE cluster_label IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_taste_vectors_user_label
  ON public.user_taste_vectors (user_id, cluster_label)
  WHERE cluster_label IS NOT NULL;

-- 8. user_taste_vectors에 HNSW 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_user_taste_vectors_hnsw
  ON public.user_taste_vectors
  USING hnsw (vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
