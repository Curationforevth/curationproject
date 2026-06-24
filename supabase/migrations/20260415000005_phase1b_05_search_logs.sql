-- Phase 1B — 05_search_logs
CREATE TABLE IF NOT EXISTS search_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  query TEXT NOT NULL,
  result_count INT NOT NULL,
  clicked_book_id UUID REFERENCES books(id) ON DELETE SET NULL,
  searched_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_user ON search_logs (user_id, searched_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_query ON search_logs USING gin (to_tsvector('simple', query));

ALTER TABLE search_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS search_insert_own ON search_logs;
CREATE POLICY search_insert_own ON search_logs FOR INSERT WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS search_read_own ON search_logs;
CREATE POLICY search_read_own ON search_logs FOR SELECT USING (auth.uid() = user_id);
