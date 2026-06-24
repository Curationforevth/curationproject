-- 적용: Supabase SQL Editor.
-- 기존 데이터 영향:
--   - 현재 각 book_id 에 1 row 뿐이면 migration 은 무손상.
--   - 만약 과거 실험으로 (book_id, tier=1) + (book_id, tier=2) 가
--     모두 존재한다면 기존 UNIQUE(book_id) 가 이미 걸렸을 것이므로
--     이 케이스는 불가능.

-- supabase/migrations/20260410_book_embeddings_tier_composite.sql
-- book_embeddings 를 (book_id, tier) 복합 unique 로 변경.
-- 동일 book 의 tier1/tier2 가 공존 가능 → taste_recomputer 가 max tier 선택.

-- 기존 UNIQUE(book_id) 제약명을 먼저 확인 후 삭제.
-- Supabase 기본 제약명: book_embeddings_book_id_key

ALTER TABLE public.book_embeddings
  DROP CONSTRAINT IF EXISTS book_embeddings_book_id_key;

-- 새 composite unique
ALTER TABLE public.book_embeddings
  ADD CONSTRAINT book_embeddings_book_id_tier_unique
  UNIQUE (book_id, tier);

-- 기존 인덱스는 그대로 유지 (idx_book_embeddings_hnsw).
