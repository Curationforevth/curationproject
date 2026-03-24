-- =============================================
-- 006: Tier 2 임베딩 파이프라인 스키마 확장
-- Spec: docs/superpowers/specs/2026-03-24-tier2-embedding-pipeline-design.md
-- =============================================

-- book_embeddings에 소스 추적 컬럼 추가
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS source_text TEXT;
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS data_sources JSONB DEFAULT '[]'::jsonb;
