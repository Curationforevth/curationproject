-- Phase 1B — 03_user_curation_history
CREATE TABLE IF NOT EXISTS user_curation_history (
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  curation_id BIGINT NOT NULL REFERENCES curation_themes(id) ON DELETE CASCADE,
  shown_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (user_id, curation_id, shown_at)
);

CREATE INDEX IF NOT EXISTS idx_uch_user_recent
  ON user_curation_history (user_id, shown_at DESC);

ALTER TABLE user_curation_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS uch_read_own ON user_curation_history;
CREATE POLICY uch_read_own ON user_curation_history FOR SELECT USING (auth.uid() = user_id);
