-- Phase 1B — 11_home_section_cache
CREATE TABLE IF NOT EXISTS home_section_cache (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  sections JSONB NOT NULL,
  tier INT NOT NULL,
  stage INT NOT NULL,
  input_hash TEXT NOT NULL,
  computed_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE home_section_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS home_cache_read_own ON home_section_cache;
CREATE POLICY home_cache_read_own ON home_section_cache FOR SELECT USING (auth.uid() = user_id);

-- INSERT/UPDATE/DELETE 는 service_role 만 (실제 쓰기 경로)
-- 명시적 policy 로 의도를 문서화
DROP POLICY IF EXISTS home_cache_service_write ON home_section_cache;
CREATE POLICY home_cache_service_write ON home_section_cache
  FOR ALL TO service_role USING (true) WITH CHECK (true);
