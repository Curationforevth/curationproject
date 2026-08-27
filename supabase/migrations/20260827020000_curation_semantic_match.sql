-- 큐레이션 테마 ↔ 책 의미 매칭 (book_embeddings tier2 활용)
--
-- 배경(2026-08-27 실측): 활성 테마 444개가 전부 keyword 타입이고 매칭이
--   library_keywords @> ARRAY[keyword]   -- 정확 문자열 포함
-- 뿐이다. 그 결과:
--   * library_keywords 보유 책 2,783 / 9,588 (29%)
--   * 큐레이션에 **노출 가능한 책 1,577권(16%)** — 나머지 84% 는 영영 안 나온다
--   * 노출 슬롯 2,796 / 고유책 1,577 = 평균 1.77배 중복(최대 14개 테마 중복 노출)
--
-- book_embeddings.tier2(제목·저자·장르·내용 융합, text-embedding-3-small 1536D)를
-- 쓰면 후보가 **5,774권(커버+tier2 보유)** 으로 3.7배가 된다. 이 테이블은 추천
-- 서빙이 읽지 않으므로(v3 는 book_v3_vectors + book_love_reasons) **추천 품질에
-- 영향이 없다.**
--
-- 프로토타입 실측 — 유사도가 품질을 잘 예측한다:
--   0.45~0.59  「경제 이야기 모음」→ 경제용어도감/아주 경제적인 하루/교양 꿀꺽 경제 (정확)
--   0.35~0.40  「진정성의 의미」  → 다정함이 인격이다/자기긍정감 (느슨하나 관련)
--   0.29~0.36  (역사 테마)       → 방어구의 역사/색깔의 역사 (약함)
-- → 임계값을 걸고 **기존 키워드 결과에 더하는** 방식이면 순증이다.
--
-- ⚠️ 계산은 SQL 이 아니라 Python 배치가 한다 — 단일 테마 순차스캔이 실측 2.56s 라
--    444개면 19분이고, hnsw 인덱스는 ~42MB 라 남은 용량(24MB)을 넘는다.
--    scripts/curation_semantic_match.py 가 행렬 한 번으로 계산해 아래 컬럼에 적재하고,
--    시간당 refresh_curation_cache_all 이 키워드 결과와 합친다.

SET statement_timeout = 0;

ALTER TABLE curation_themes
  ADD COLUMN IF NOT EXISTS theme_embedding halfvec(1536),
  ADD COLUMN IF NOT EXISTS semantic_book_ids jsonb,
  ADD COLUMN IF NOT EXISTS semantic_updated_at timestamptz;

COMMENT ON COLUMN curation_themes.theme_embedding IS
  '테마 텍스트(title + keyword)의 text-embedding-3-small(1536D) 임베딩. book_embeddings.tier2 와 같은 모델이어야 비교가 성립한다 — 다른 모델로 임베딩하면 유사도가 0.05 수준으로 무너진다(실측).';
COMMENT ON COLUMN curation_themes.semantic_book_ids IS
  '의미 매칭으로 뽑은 book_id 배열(임계값 통과분). refresh_curation_cache_all 이 키워드 결과와 합집합한다.';

-- 키워드 결과 ∪ 의미 매칭 결과 (의미분은 뒤에 붙여 기존 노출 순서를 보존)
CREATE OR REPLACE FUNCTION public.refresh_curation_cache_all()
RETURNS void
LANGUAGE plpgsql
AS $function$
DECLARE
  theme RECORD;
  book_ids UUID[];
  sem_ids UUID[];
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

   -- 의미 매칭 보강: 커버 있고 아직 안 담긴 책만 뒤에 덧붙인다(기존 순서 보존).
   IF theme.semantic_book_ids IS NOT NULL THEN
     SELECT array_agg(x) INTO sem_ids
     FROM (SELECT (jsonb_array_elements_text(theme.semantic_book_ids))::uuid x) t
     WHERE NOT (x = ANY(COALESCE(book_ids, ARRAY[]::uuid[])))
       AND EXISTS (SELECT 1 FROM books b WHERE b.id = x AND b.cover_url IS NOT NULL);
     IF sem_ids IS NOT NULL THEN
       book_ids := (COALESCE(book_ids, ARRAY[]::uuid[]) || sem_ids)[1:theme.max_books];
     END IF;
   END IF;

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
$function$;
