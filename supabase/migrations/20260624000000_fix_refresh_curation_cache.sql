-- Fix: refresh_curation_cache_all() 가 모든 테마에서 조용히 실패하던 버그.
--
-- 원인: genre_combo/author/keyword 분기의 inner 서브쿼리가 `SELECT id` 만 뽑는데
-- outer `array_agg(id ORDER BY loan_count DESC)` 가 서브쿼리에 없는 loan_count 를
-- 참조 → "column loan_count does not exist" 예외 → per-theme EXCEPTION 핸들러가
-- 삼킴 → curation_cache 한 건도 안 채워지고 테마도 비활성화 안 됨(증상: cache 0행,
-- active 그대로). hourly cron 도 동일하게 매번 무음 실패.
--
-- 수정: inner 서브쿼리에 loan_count 를 포함시켜 outer ORDER BY 가 참조 가능하게 함.
-- cluster 분기도 함께 정정: 기존엔 LIMIT 이 집계(1행) 결과에 걸려 max_books 제한이
-- 무효였음 → 서브쿼리로 감싸 distance 기준 max_books 만 선택하도록 수정.
--
-- 원본: 20260415000012_phase1b_12_functions_curation.sql (immutable, forward-fix 로 덮음)
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
          FROM (SELECT id, loan_count FROM books
                WHERE l1 = theme.parameters->>'l1'
                  AND l2 = theme.parameters->>'l2'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'author' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id, loan_count FROM books
                WHERE author = theme.parameters->>'author'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'keyword' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id, loan_count FROM books
                WHERE library_keywords @> ARRAY[theme.parameters->>'keyword']
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'cluster' THEN
          SELECT array_agg(book_id ORDER BY distance) INTO book_ids
          FROM (SELECT book_id, distance FROM book_cluster_assignments
                WHERE cluster_id = (theme.parameters->>'cluster_id')::int
                  AND cluster_version = theme.parameters->>'cluster_version'
                ORDER BY distance
                LIMIT theme.max_books) s;
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
