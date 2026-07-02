-- 대표 저자 정규화 — 홈 스크린샷 리포트(2026-07-02) 후속.
--
-- 문제: books.author 가 소스별로 다름(data4library='한강' / aladin='애거서 크리스티
-- (지은이), 공경희 (옮긴이)'). top_authors(user_state)와 curation_themes.target_author
-- 가 원문 그대로라 ① author 테마 제목 오염(44/61개) ② 같은 저자의 카운트가 표기별로
-- 쪼개짐 ③ by_author 개인화 매칭이 표기 불일치로 샘.
--
-- 수정: normalize_primary_author() 를 정본 규칙으로 두고(첫 저자 + 역할 괄호 제거),
-- top_authors 집계를 정규화 기준으로 재정의. 테마 생성(Python)·앱 표시(Dart)도
-- 동일 규칙 사용(generate_curation_themes.py / author_format.dart — 동등성 테스트로 동기화).

BEGIN;

-- 정본 정규화 규칙: 콤마 앞 첫 저자 → 괄호(역할 표기) 제거 → 꼬리 역할어
-- ('요한 하리 지음' 류, 실데이터 리허설에서 발견) 제거 → trim. 빈 결과는 NULL.
CREATE OR REPLACE FUNCTION normalize_primary_author(raw TEXT) RETURNS TEXT AS $$
  SELECT NULLIF(trim(regexp_replace(
    regexp_replace(split_part(raw, ',', 1), '\s*\([^)]*\)', '', 'g'),
    '\s+(지음|옮김|엮음|글|그림)\s*$', '')), '')
$$ LANGUAGE sql IMMUTABLE;

-- top_authors 를 정규화 저자 기준으로 집계 (표기 변형 간 카운트 병합)
CREATE OR REPLACE FUNCTION refresh_user_top_taste_single(target_user_id UUID) RETURNS void AS $$
BEGIN
  UPDATE user_state SET
    top_authors = COALESCE((
      SELECT jsonb_agg(jsonb_build_object('author', author, 'count', cnt))
      FROM (
        SELECT normalize_primary_author(b.author) AS author, COUNT(*) as cnt
        FROM user_books ub JOIN books b ON ub.book_id = b.id
        WHERE ub.user_id = target_user_id AND ub.rating = 'good'
          AND normalize_primary_author(b.author) IS NOT NULL
        GROUP BY normalize_primary_author(b.author)
        ORDER BY cnt DESC
        LIMIT 5
      ) top
    ), '[]'::jsonb),
    top_l1s = COALESCE((
      SELECT jsonb_agg(jsonb_build_object('l1', l1, 'count', cnt))
      FROM (
        SELECT b.l1, COUNT(*) as cnt
        FROM user_books ub JOIN books b ON ub.book_id = b.id
        WHERE ub.user_id = target_user_id AND ub.rating = 'good' AND b.l1 IS NOT NULL
        GROUP BY b.l1
        ORDER BY cnt DESC
        LIMIT 3
      ) top
    ), '[]'::jsonb),
    updated_at = NOW()
  WHERE user_id = target_user_id;
END;
$$ LANGUAGE plpgsql;

-- 오염된 author 테마 비활성화(가역 — 행 보존). 정규화된 클린 테마는 insert-only
-- 생성기(주간 워크플로)가 새 theme_key 로 넣는다.
UPDATE curation_themes
SET is_active = FALSE
WHERE theme_type = 'author' AND target_author ~ '\(';

-- 기존 유저 top_authors 즉시 재계산(트리거/데일리 대기 없이 일관성 확보)
SELECT refresh_user_top_taste_all();

COMMIT;
