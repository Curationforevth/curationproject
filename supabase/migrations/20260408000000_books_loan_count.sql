-- 20260408_books_loan_count.sql
-- 정보나루 인기대출 카운트를 books에 저장 (sales_point와 별개)
BEGIN;

ALTER TABLE public.books
  ADD COLUMN IF NOT EXISTS loan_count INT;

CREATE INDEX IF NOT EXISTS idx_books_loan_count
  ON public.books (loan_count DESC NULLS LAST);

COMMIT;
