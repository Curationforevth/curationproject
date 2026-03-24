-- =============================================
-- 007: rich_description 컬럼 추가
-- YES24 스크래핑 데이터 저장용
-- =============================================

-- books 테이블에 rich_description 컬럼 추가
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS rich_description TEXT;

-- yes24_scraper, tier2_embedder 쿼리 최적화용 부분 인덱스
CREATE INDEX IF NOT EXISTS idx_books_rich_description_null
ON public.books (sales_point DESC NULLS LAST)
WHERE rich_description IS NULL;
