-- supabase/migrations/20260408_fallback_curation.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.fallback_curation (
  id BIGSERIAL PRIMARY KEY,
  rank INT NOT NULL,
  book_id UUID NOT NULL REFERENCES public.books(id) ON DELETE CASCADE,
  loan_count INT,
  added_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  UNIQUE (book_id)
);

CREATE INDEX IF NOT EXISTS idx_fallback_rank
  ON public.fallback_curation (rank ASC);

ALTER TABLE public.fallback_curation ENABLE ROW LEVEL SECURITY;

CREATE POLICY "모두 읽기" ON public.fallback_curation
  FOR SELECT USING (true);

COMMIT;
