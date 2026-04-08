-- 20260407_phase1a_recommendation_impressions.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.recommendation_impressions (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  book_id UUID NOT NULL REFERENCES public.books(id) ON DELETE CASCADE,
  position INT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('home_recommend','similar','curation','search')),
  algorithm_version TEXT NOT NULL,
  shown_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  action TEXT CHECK (action IS NULL OR action IN ('clicked','saved','liked','disliked','ignored')),
  action_at TIMESTAMPTZ,
  session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_imp_user_time
  ON public.recommendation_impressions (user_id, shown_at DESC);
CREATE INDEX IF NOT EXISTS idx_imp_book
  ON public.recommendation_impressions (book_id);
CREATE INDEX IF NOT EXISTS idx_imp_unactioned
  ON public.recommendation_impressions (user_id, shown_at DESC)
  WHERE action IS NULL;

ALTER TABLE public.recommendation_impressions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "유저는 본인 impression INSERT" ON public.recommendation_impressions
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "유저는 본인 impression UPDATE" ON public.recommendation_impressions
  FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "유저는 본인 impression SELECT" ON public.recommendation_impressions
  FOR SELECT USING (auth.uid() = user_id);

COMMIT;
