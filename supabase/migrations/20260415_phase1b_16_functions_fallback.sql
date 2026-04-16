-- Phase 1B — 16_functions_fallback
-- fallback_curation top 30 by loan_count (제목 기준 중복 제거)
CREATE OR REPLACE FUNCTION refresh_fallback_curation() RETURNS void AS $$
BEGIN
  DELETE FROM fallback_curation;
  INSERT INTO fallback_curation (rank, book_id, loan_count, added_at)
    SELECT
      ROW_NUMBER() OVER (ORDER BY loan_count DESC NULLS LAST, id),
      id,
      loan_count,
      NOW()
    FROM (
      SELECT DISTINCT ON (title) id, title, loan_count
      FROM books
      WHERE loan_count IS NOT NULL
      ORDER BY title, loan_count DESC NULLS LAST
    ) deduped
    ORDER BY loan_count DESC NULLS LAST
    LIMIT 30;
END;
$$ LANGUAGE plpgsql;
