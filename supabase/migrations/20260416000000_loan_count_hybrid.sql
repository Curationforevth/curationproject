-- Strategy C — 정보나루 + 알라딘 혼합 fallback_curation.
-- Spec: docs/superpowers/specs/2026-04-16-data4library-aladin-hybrid-collection.md
BEGIN;

-- ── 스키마 확장: loan_count 소스 추적 + 최근 12개월 대출수 ──
ALTER TABLE books
  ADD COLUMN IF NOT EXISTS loan_count_12mo INT,
  ADD COLUMN IF NOT EXISTS loan_count_source TEXT,
  ADD COLUMN IF NOT EXISTS loan_count_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_books_loan_count_12mo
  ON books (loan_count_12mo DESC NULLS LAST);

-- ── refresh_fallback_curation() 재작성 (Strategy C) ──
-- 정보나루 loan_count_12mo top 20 (제목 dedup)
-- + 알라딘 sales_point top 10 (정보나루에 없는 책만)
-- = 총 30권.
CREATE OR REPLACE FUNCTION refresh_fallback_curation() RETURNS void AS $$
BEGIN
  DELETE FROM fallback_curation;

  INSERT INTO fallback_curation (rank, book_id, loan_count, added_at)
    WITH d4l AS (
      SELECT DISTINCT ON (title) id, title, loan_count_12mo, loan_count
      FROM books
      WHERE loan_count_12mo IS NOT NULL
        AND title IS NOT NULL
      ORDER BY title, loan_count_12mo DESC NULLS LAST
    ),
    d4l_top AS (
      SELECT id, title, loan_count_12mo AS sort_val, loan_count, 1 AS priority
      FROM d4l
      ORDER BY loan_count_12mo DESC NULLS LAST
      LIMIT 20
    ),
    -- 알라딘 sales_point top 중 정보나루 top 20 에 같은 제목이 없는 책만.
    -- NOT EXISTS 사용 (NOT IN + NULL title 조합은 전체 조건을 UNKNOWN 으로
    -- 만들어 알라딘 보완분이 통째로 빠지는 함정이 있음).
    aladin_new AS (
      SELECT b.id, b.sales_point AS sort_val, b.loan_count, 2 AS priority
      FROM books b
      WHERE b.sales_point IS NOT NULL
        AND b.sales_point > 0
        AND b.title IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM d4l_top dt WHERE dt.title = b.title
        )
      ORDER BY b.sales_point DESC
      LIMIT 10
    ),
    combined AS (
      SELECT id, sort_val, loan_count, priority FROM d4l_top
      UNION ALL
      SELECT id, sort_val, loan_count, priority FROM aladin_new
    )
    SELECT
      ROW_NUMBER() OVER (ORDER BY priority, sort_val DESC NULLS LAST),
      id,
      loan_count,
      NOW()
    FROM combined
    LIMIT 30;
END;
$$ LANGUAGE plpgsql;

COMMIT;
