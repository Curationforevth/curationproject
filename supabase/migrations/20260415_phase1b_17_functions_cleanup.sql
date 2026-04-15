-- Phase 1B — 17_functions_cleanup
CREATE OR REPLACE FUNCTION cleanup_user_curation_history() RETURNS void AS $$
BEGIN
  DELETE FROM user_curation_history WHERE shown_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cleanup_home_section_cache() RETURNS void AS $$
BEGIN
  DELETE FROM home_section_cache WHERE computed_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;
