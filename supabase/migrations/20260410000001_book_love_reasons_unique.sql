-- 적용: Supabase 대시보드 → SQL Editor → 실행.
-- 기존 중복 에러 발생 시:
--   DELETE FROM public.book_love_reasons a USING public.book_love_reasons b
--   WHERE a.id > b.id
--     AND a.book_id = b.book_id
--     AND a.source = b.source
--     AND a.reason = b.reason;
--   그 후 ALTER TABLE 재시도.

-- supabase/migrations/20260410_book_love_reasons_unique.sql
-- book_love_reasons: 같은 책 + 같은 source 에서 동일 reason 중복 방지.
-- 영향: reason_extractor(v1, source='llm_extracted') 와
--       v3_reason_extract(source='v3_context_rich') 는 독립적으로 공존.
-- 기존 중복 데이터가 있으면 migration 실패 → Eden 수동 정리 후 재시도.

ALTER TABLE public.book_love_reasons
  ADD CONSTRAINT book_love_reasons_book_source_reason_unique
  UNIQUE (book_id, source, reason);
