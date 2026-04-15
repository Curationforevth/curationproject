-- Phase 1B — 01_curation_themes
-- Spec Section 5.2 curation_themes
CREATE TABLE IF NOT EXISTS curation_themes (
  id BIGSERIAL PRIMARY KEY,
  theme_key TEXT UNIQUE NOT NULL,
  theme_type TEXT NOT NULL CHECK (theme_type IN ('genre_combo','author','keyword','cluster')),
  title TEXT NOT NULL,
  description TEXT,
  selection_query JSONB NOT NULL,
  parameters JSONB,
  min_books INT DEFAULT 5,
  max_books INT DEFAULT 30,
  priority FLOAT DEFAULT 1.0,
  personalization TEXT DEFAULT 'general'
    CHECK (personalization IN ('general','tier1+','tier2+','by_l1','by_author','by_keyword')),
  target_l1 TEXT,
  target_author TEXT,
  target_keyword TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_shown_at TIMESTAMPTZ,
  shown_count INT DEFAULT 0,
  click_count INT DEFAULT 0,
  click_rate FLOAT GENERATED ALWAYS AS (
    COALESCE(click_count::float / NULLIF(shown_count, 0), 0.0)
  ) STORED
);

CREATE INDEX IF NOT EXISTS idx_curation_active_type
  ON curation_themes (theme_type) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_personalization
  ON curation_themes (personalization) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_target_l1
  ON curation_themes (target_l1) WHERE target_l1 IS NOT NULL AND is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_target_author
  ON curation_themes (target_author) WHERE target_author IS NOT NULL AND is_active = TRUE;

ALTER TABLE curation_themes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS curation_themes_read ON curation_themes;
CREATE POLICY curation_themes_read ON curation_themes FOR SELECT USING (is_active = TRUE);
