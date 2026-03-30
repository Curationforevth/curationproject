-- supabase/010_love_reasons.sql
-- =============================================
-- 010: 추천 엔진 v2 — "좋아할 이유" 기반 매칭
-- Spec: docs/superpowers/specs/2026-03-26-recommendation-engine-v2-design.md
-- =============================================

-- 1. 책의 "좋아할 이유"
CREATE TABLE IF NOT EXISTS public.book_love_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id UUID REFERENCES public.books(id) NOT NULL,
  reason TEXT NOT NULL,
  reason_embedding VECTOR(2000),
  source TEXT NOT NULL DEFAULT 'llm_extracted',
  user_mention_count INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_blr_book ON public.book_love_reasons(book_id);
CREATE INDEX IF NOT EXISTS idx_blr_embedding
  ON public.book_love_reasons USING hnsw (reason_embedding vector_cosine_ops);

-- 2. 유저의 "좋아하는 이유"
CREATE TABLE IF NOT EXISTS public.user_taste_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  book_id UUID REFERENCES public.books(id) NOT NULL,
  reason TEXT NOT NULL,
  reason_embedding VECTOR(2000),
  weight FLOAT NOT NULL DEFAULT 1.0,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_utr_user ON public.user_taste_reasons(user_id);
CREATE INDEX IF NOT EXISTS idx_utr_embedding
  ON public.user_taste_reasons USING hnsw (reason_embedding vector_cosine_ops);

-- 3. RPC: 이유 기반 추천 (min-of-max 교집합 스코어링)
--
-- 스코어링 로직:
--   유저의 각 취향 reason에 대해 책별 best similarity를 구하고,
--   그 중 가장 약한 축(MIN)을 기준으로 순위를 매긴다.
--   → "모든 취향 축을 고르게 만족하는 책"이 상위에 올라온다.
--
--   reason이 1개뿐이면 MIN = MAX이므로 단일 축 매칭과 동일하게 동작.
--   reason이 여러 개일 때, 한 축만 강하고 나머지가 약한 책은 밀린다.
--
-- 변경 이력:
--   v1: avg * 0.7 + max * 0.3 — 한 축 편향 문제 (SF가 강하면 퀴어 없어도 상위)
--   v2: MIN(best_per_reason) — 교집합 매칭 (퀴어+SF 둘 다 만족해야 상위)
CREATE OR REPLACE FUNCTION public.recommend_books_by_reasons(
  p_user_id UUID,
  p_match_count INT DEFAULT 20
)
RETURNS TABLE (book_id UUID, title TEXT, score FLOAT, matched_reason TEXT)
AS $$
  WITH user_reasons AS (
    SELECT id AS reason_id, reason_embedding, weight
    FROM public.user_taste_reasons
    WHERE user_id = p_user_id AND weight > 0
  ),
  reason_count AS (
    SELECT COUNT(*) AS n FROM user_reasons
  ),
  raw_matches AS (
    SELECT
      ur.reason_id,
      ur.weight,
      blr.book_id,
      1 - (blr.reason_embedding <=> ur.reason_embedding) AS similarity,
      blr.reason AS matched_reason
    FROM user_reasons ur
    CROSS JOIN LATERAL (
      SELECT book_id, reason_embedding, reason
      FROM public.book_love_reasons
      ORDER BY reason_embedding <=> ur.reason_embedding
      LIMIT 200
    ) blr
  ),
  best_per_pair AS (
    SELECT DISTINCT ON (reason_id, book_id)
      reason_id, book_id, weight, similarity, matched_reason
    FROM raw_matches
    ORDER BY reason_id, book_id, similarity DESC
  ),
  book_scores AS (
    SELECT
      bpp.book_id,
      -- 교집합 스코어: 가장 약한 축 기준
      MIN(bpp.weight * bpp.similarity) AS min_score,
      -- 보조 스코어: 평균 (동점 시 tiebreaker)
      AVG(bpp.weight * bpp.similarity) AS avg_score,
      -- 매칭된 축 수
      COUNT(DISTINCT bpp.reason_id) AS matched_axes,
      (ARRAY_AGG(bpp.matched_reason ORDER BY bpp.weight * bpp.similarity DESC))[1] AS top_reason
    FROM best_per_pair bpp
    WHERE bpp.book_id NOT IN (
      SELECT ub.book_id FROM public.user_books ub WHERE ub.user_id = p_user_id
    )
    AND bpp.book_id NOT IN (
      SELECT b.id FROM public.books b WHERE b.canonical_book_id IS NOT NULL
    )
    GROUP BY bpp.book_id
  )
  SELECT bs.book_id, b.title,
    -- 모든 축에 매칭된 책 우선, 그 안에서 min_score 순
    -- 부분 매칭은 min_score * (matched_axes / total_axes) 로 페널티
    (bs.min_score * (bs.matched_axes::FLOAT / rc.n) * 0.8
     + bs.avg_score * 0.2)::FLOAT AS score,
    bs.top_reason AS matched_reason
  FROM book_scores bs
  JOIN public.books b ON b.id = bs.book_id
  CROSS JOIN reason_count rc
  ORDER BY score DESC
  LIMIT p_match_count;
$$ LANGUAGE sql STABLE;

-- 4. RLS
ALTER TABLE public.book_love_reasons ENABLE ROW LEVEL SECURITY;
CREATE POLICY "book_love_reasons_read" ON public.book_love_reasons
  FOR SELECT USING (true);
CREATE POLICY "book_love_reasons_service" ON public.book_love_reasons
  FOR ALL USING (auth.role() = 'service_role');

ALTER TABLE public.user_taste_reasons ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user_taste_reasons_own" ON public.user_taste_reasons
  FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_taste_reasons_service" ON public.user_taste_reasons
  FOR ALL USING (auth.role() = 'service_role');
