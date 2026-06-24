-- Phase 1B — 07_user_state_extend
-- top_authors: [{"author": "...", "count": N}, ...] top 5
-- top_l1s: [{"l1": "문학", "count": N}, ...] top 3
ALTER TABLE user_state
  ADD COLUMN IF NOT EXISTS top_authors JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS top_l1s JSONB DEFAULT '[]'::jsonb;
