-- books.source check constraint에 'data4library' 추가.
-- 정보나루 발견 수집기(data4library_discovery_collector)가 source='data4library'로 저장.
--
-- 적용: Supabase 대시보드 → SQL Editor → 실행.

ALTER TABLE public.books DROP CONSTRAINT IF EXISTS books_source_check;
ALTER TABLE public.books ADD CONSTRAINT books_source_check
  CHECK (source IN ('kakao', 'aladin', 'data4library'));
