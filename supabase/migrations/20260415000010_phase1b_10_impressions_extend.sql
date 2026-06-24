-- Phase 1B — 10_impressions_extend
ALTER TABLE recommendation_impressions
  ADD COLUMN IF NOT EXISTS curation_id BIGINT REFERENCES curation_themes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_imp_curation
  ON recommendation_impressions (curation_id, shown_at DESC)
  WHERE curation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_imp_shown_at
  ON recommendation_impressions (shown_at);
