-- Migration: recommend_books_by_reasons v4 — 브릿지 매칭
-- Date: 2026-03-30
--
-- 핵심 변경:
--   유저 피드백 → LLM이 좋아한 책의 reason 선택 → 태그↔태그 매칭으로 추천
--   user_taste_reasons에는 선택된 reason(=좋아한 책의 reason)이 저장됨
--   reason_embedding은 좋아한 책의 reason embedding을 그대로 복사
--
-- 스코어링:
--   각 유저 마커(선택된 reason)별로 후보 책의 best match 계산 (태그↔태그)
--   전체 마커의 best match 평균 × (1 + 장르 부스트)
--   장르 부스트: 유저가 읽은 책들의 장르와 후보 책 장르의 분류 체계 겹침
--
-- 장르 필터: 유아/어린이/좋은부모만 hard exclude
--
-- 실행: Supabase SQL Editor에서 이 파일 전체를 실행

CREATE OR REPLACE FUNCTION public.recommend_books_by_reasons(
  p_user_id UUID,
  p_match_count INT DEFAULT 20
)
RETURNS TABLE (book_id UUID, title TEXT, score FLOAT, matched_reason TEXT)
AS $$
  WITH user_markers AS (
    -- 유저의 취향 마커 (LLM이 선택한 reason들, embedding은 원본 book reason의 것)
    SELECT id, book_id AS source_book_id, reason_embedding, weight
    FROM public.user_taste_reasons
    WHERE user_id = p_user_id AND weight > 0
  ),
  marker_count AS (
    SELECT COUNT(*) AS n FROM user_markers
  ),
  -- 유저가 읽은 책들의 장르 수집
  user_genre_raw AS (
    SELECT DISTINCT unnest(string_to_array(b.genre, '>')) AS part
    FROM public.user_books ub
    JOIN public.books b ON b.id = ub.book_id
    WHERE ub.user_id = p_user_id
  ),
  user_genre_parts AS (
    SELECT part FROM user_genre_raw WHERE part != '국내도서'
  ),
  -- 태그↔태그 매칭: 각 마커별 가장 가까운 book reason 200개
  raw_matches AS (
    SELECT
      um.id AS marker_id,
      um.weight,
      blr.book_id,
      1 - (blr.reason_embedding <=> um.reason_embedding) AS similarity,
      blr.reason AS matched_reason
    FROM user_markers um
    CROSS JOIN LATERAL (
      SELECT book_id, reason_embedding, reason
      FROM public.book_love_reasons
      WHERE book_id NOT IN (
        SELECT ub2.book_id FROM public.user_books ub2 WHERE ub2.user_id = p_user_id
      )
      ORDER BY reason_embedding <=> um.reason_embedding
      LIMIT 200
    ) blr
  ),
  -- 마커별 책별 best match
  best_per_pair AS (
    SELECT DISTINCT ON (marker_id, book_id)
      marker_id, book_id, weight, similarity, matched_reason
    FROM raw_matches
    ORDER BY marker_id, book_id, similarity DESC
  ),
  -- 책별 종합 스코어
  book_scores AS (
    SELECT
      bpp.book_id,
      AVG(bpp.weight * bpp.similarity) AS avg_sim,
      COUNT(DISTINCT bpp.marker_id) AS matched_markers,
      (ARRAY_AGG(bpp.matched_reason ORDER BY bpp.weight * bpp.similarity DESC))[1] AS top_reason
    FROM best_per_pair bpp
    -- 유아/어린이/좋은부모 hard exclude
    WHERE NOT EXISTS (
      SELECT 1 FROM public.books b2
      WHERE b2.id = bpp.book_id
      AND (b2.genre ILIKE '%유아%' OR b2.genre ILIKE '%어린이%' OR b2.genre ILIKE '%좋은부모%')
    )
    -- canonical 중복 제외
    AND NOT EXISTS (
      SELECT 1 FROM public.books b3
      WHERE b3.id = bpp.book_id AND b3.canonical_book_id IS NOT NULL
    )
    GROUP BY bpp.book_id
  ),
  -- 장르 부스트 계산
  genre_boost AS (
    SELECT
      bs.book_id,
      COALESCE(
        (SELECT COUNT(DISTINCT ugp.part)
         FROM user_genre_parts ugp
         WHERE b.genre ILIKE '%' || ugp.part || '%'
        ) * 0.1,
        0
      ) AS boost
    FROM book_scores bs
    JOIN public.books b ON b.id = bs.book_id
  )
  SELECT
    bs.book_id,
    b.title,
    (bs.avg_sim * (1 + LEAST(COALESCE(gb.boost, 0), 0.3)))::FLOAT AS score,
    bs.top_reason AS matched_reason
  FROM book_scores bs
  JOIN public.books b ON b.id = bs.book_id
  LEFT JOIN genre_boost gb ON gb.book_id = bs.book_id
  CROSS JOIN marker_count mc
  ORDER BY score DESC
  LIMIT p_match_count;
$$ LANGUAGE sql STABLE;
