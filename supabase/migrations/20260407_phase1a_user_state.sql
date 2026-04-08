-- 20260407_phase1a_user_state.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.user_state (
  user_id UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  total_likes INT DEFAULT 0 NOT NULL,
  total_saves INT DEFAULT 0 NOT NULL,
  total_finished INT DEFAULT 0 NOT NULL,
  consecutive_ignores INT DEFAULT 0 NOT NULL,
  last_active_at TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT FALSE NOT NULL,
  current_tier INT DEFAULT 0 NOT NULL CHECK (current_tier IN (0,1,2)),
  updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_state_active
  ON public.user_state (is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_user_state_tier
  ON public.user_state (current_tier);

ALTER TABLE public.user_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY "유저는 본인 state 조회" ON public.user_state
  FOR SELECT USING (auth.uid() = user_id);

COMMIT;
