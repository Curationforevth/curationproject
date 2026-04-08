-- 20260407_phase1a_status_normalize.sql
-- user_books.status / rating 값을 v3 spec 모델로 정규화

BEGIN;

-- 1. 기존 status check constraint 제거
ALTER TABLE public.user_books DROP CONSTRAINT IF EXISTS user_books_status_check;

-- 2. 값 매핑
UPDATE public.user_books SET status = 'finished' WHERE status = 'read';
UPDATE public.user_books SET status = 'wishlist' WHERE status = 'want_to_read';
-- 'reading' 은 그대로

-- 3. 새 default + check constraint
ALTER TABLE public.user_books ALTER COLUMN status SET DEFAULT 'wishlist';
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_status_check
  CHECK (status IN ('wishlist', 'reading', 'finished'));

-- 4. rating: 'neutral' 제거 → null
UPDATE public.user_books SET rating = NULL WHERE rating = 'neutral';

ALTER TABLE public.user_books DROP CONSTRAINT IF EXISTS user_books_rating_check;
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_rating_check
  CHECK (rating IS NULL OR rating IN ('good', 'bad'));

-- 5. wishlist 행은 rating null 강제
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_wishlist_no_rating
  CHECK (status <> 'wishlist' OR rating IS NULL);

COMMIT;
