-- 20260407_phase1a_user_books_history.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.user_books_history (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL,
  old_status TEXT,
  new_status TEXT,
  old_rating TEXT,
  new_rating TEXT,
  changed_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_user_book
  ON public.user_books_history (user_id, book_id, changed_at DESC);

-- RLS: 본인 기록만 read
ALTER TABLE public.user_books_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "유저는 본인 history만 조회" ON public.user_books_history
  FOR SELECT USING (auth.uid() = user_id);

-- trigger function: status 또는 rating 이 바뀐 경우만 기록
CREATE OR REPLACE FUNCTION public.log_user_books_change()
RETURNS TRIGGER AS $$
BEGIN
  IF (OLD.status IS DISTINCT FROM NEW.status)
     OR (OLD.rating IS DISTINCT FROM NEW.rating) THEN
    INSERT INTO public.user_books_history
      (user_id, book_id, old_status, new_status, old_rating, new_rating)
    VALUES
      (NEW.user_id, NEW.book_id, OLD.status, NEW.status, OLD.rating, NEW.rating);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS user_books_audit ON public.user_books;
CREATE TRIGGER user_books_audit
  AFTER UPDATE ON public.user_books
  FOR EACH ROW EXECUTE FUNCTION public.log_user_books_change();

-- INSERT 시에도 첫 상태 기록 (분석 일관성)
CREATE OR REPLACE FUNCTION public.log_user_books_insert()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_books_history
    (user_id, book_id, old_status, new_status, old_rating, new_rating)
  VALUES
    (NEW.user_id, NEW.book_id, NULL, NEW.status, NULL, NEW.rating);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS user_books_audit_insert ON public.user_books;
CREATE TRIGGER user_books_audit_insert
  AFTER INSERT ON public.user_books
  FOR EACH ROW EXECUTE FUNCTION public.log_user_books_insert();

COMMIT;
