-- Phase 1B — 12_functions_curation
-- refresh_curation_cache_all: hourly, per-theme exception handling
CREATE OR REPLACE FUNCTION refresh_curation_cache_all() RETURNS void AS $$
DECLARE
  theme RECORD;
  book_ids UUID[];
BEGIN
  FOR theme IN SELECT * FROM curation_themes WHERE is_active=TRUE LOOP
    BEGIN
      book_ids := NULL;
      CASE theme.theme_type
        WHEN 'genre_combo' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE l1 = theme.parameters->>'l1'
                  AND l2 = theme.parameters->>'l2'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'author' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE author = theme.parameters->>'author'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'keyword' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE library_keywords @> ARRAY[theme.parameters->>'keyword']
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'cluster' THEN
          SELECT array_agg(book_id ORDER BY distance) INTO book_ids
          FROM book_cluster_assignments
          WHERE cluster_id = (theme.parameters->>'cluster_id')::int
            AND cluster_version = theme.parameters->>'cluster_version'
          LIMIT theme.max_books;
      END CASE;

      IF array_length(book_ids, 1) >= theme.min_books THEN
        INSERT INTO curation_cache (curation_id, book_ids, cached_at, expires_at)
        VALUES (theme.id, to_jsonb(book_ids), NOW(), NOW() + INTERVAL '1 hour')
        ON CONFLICT (curation_id) DO UPDATE
          SET book_ids = EXCLUDED.book_ids,
              cached_at = NOW(),
              expires_at = EXCLUDED.expires_at;
      ELSE
        UPDATE curation_themes SET is_active = FALSE WHERE id = theme.id;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'Failed theme %: %', theme.id, SQLERRM;
      CONTINUE;
    END;
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- deactivate_curations: daily
CREATE OR REPLACE FUNCTION deactivate_curations() RETURNS void AS $$
BEGIN
  UPDATE curation_themes SET is_active = FALSE
    WHERE is_active = TRUE
      AND shown_count = 0
      AND created_at < NOW() - INTERVAL '30 days';

  UPDATE curation_themes SET is_active = FALSE
    WHERE is_active = TRUE
      AND shown_count >= 100
      AND click_rate < 0.005
      AND created_at < NOW() - INTERVAL '90 days';
END;
$$ LANGUAGE plpgsql;
