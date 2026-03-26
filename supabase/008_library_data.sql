-- =============================================
-- 008: 정보나루 도서관 데이터 컬럼 추가
-- Spec: docs/superpowers/specs/2026-03-25-data4library-integration-design.md
-- =============================================

-- 정보나루 키워드 (compose_embedding에서 임베딩 텍스트에 포함)
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS library_keywords TEXT[];

-- 함께 빌린 책 ISBN 목록 (Phase 3 추천 엔진용, co_loan 타입 확장 대비 jsonb)
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS related_isbns JSONB;
