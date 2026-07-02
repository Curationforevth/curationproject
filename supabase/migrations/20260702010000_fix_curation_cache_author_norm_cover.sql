-- 큐레이션 캐시: ① author 매칭을 정규화 기준으로 ② 커버 필수 ③ 새 author 테마 구제.
--
-- 배경(2026-07-02, Eden "이미지 안 나옴" 리포트): 저자 정규화(20260702000000) 후
-- 재생성된 author 테마 484개의 parameters->>'author' 는 정규화 값('애거서 크리스티')
-- 인데 refresh_curation_cache_all() 의 author 분기가 books.author 원문 정확일치라
-- 매칭 0 → min_books 미달 → hourly cron 이 새 테마를 자동 비활성화하는 함정.
-- 또한 캐시 선정에 cover_url 조건이 없어 커버 없는(minimal) 책이 홈 서가에 그대로
-- 노출됐다(실측: 큐레이션 캐시 앞 10권 중 커버 5/10). 홈=비주얼 서가(핵심가치 1)
-- 이므로 큐레이션 캐시는 커버 있는 책만 담는다(데이터 자체는 보존 — 노출만 제한).
--
-- 원본: 20260624000000_fix_refresh_curation_cache.sql (forward-fix 로 덮음)

BEGIN;

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
                  AND cover_url IS NOT NULL
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'author' THEN
          -- 정규화 대표저자 매칭 — 소스별 표기('한강 (지은이)' 등) 전부 흡수
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id, loan_count FROM books
                WHERE normalize_primary_author(author) = theme.parameters->>'author'
                  AND cover_url IS NOT NULL
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'keyword' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id, loan_count FROM books
                WHERE library_keywords @> ARRAY[theme.parameters->>'keyword']
                  AND cover_url IS NOT NULL
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'cluster' THEN
          SELECT array_agg(book_id ORDER BY distance) INTO book_ids
          FROM (SELECT a.book_id, a.distance
                FROM book_cluster_assignments a JOIN books b ON b.id = a.book_id
                WHERE a.cluster_id = (theme.parameters->>'cluster_id')::int
                  AND a.cluster_version = theme.parameters->>'cluster_version'
                  AND b.cover_url IS NOT NULL
                ORDER BY a.distance
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

-- 매칭 함정으로 hourly cron 에 이미 비활성화됐을 수 있는 클린 author 테마 구제.
-- (min_books 미달로 정당하게 꺼질 테마는 아래 즉시 실행이 다시 정확히 거른다)
UPDATE curation_themes
SET is_active = TRUE
WHERE theme_type = 'author' AND target_author !~ '\(' AND is_active = FALSE;

-- 즉시 1회 실행 — 새 author 테마 캐시 채움 + 커버 필터 반영(다음 hourly 안 기다림)
SELECT refresh_curation_cache_all();

COMMIT;
