-- Phase 1B — 02_curation_cache
CREATE TABLE IF NOT EXISTS curation_cache (
  curation_id BIGINT PRIMARY KEY REFERENCES curation_themes(id) ON DELETE CASCADE,
  book_ids JSONB NOT NULL,
  cached_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_curation_cache_expires ON curation_cache (expires_at);

ALTER TABLE curation_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS curation_cache_read ON curation_cache;
CREATE POLICY curation_cache_read ON curation_cache FOR SELECT USING (TRUE);
