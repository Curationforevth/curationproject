-- Phase 1B — 08_books_genre_split
-- books.genre = "대분류>소분류". generated column으로 l1/l2 분리.
-- l1: 대분류 (genre에 '>'가 없으면 genre 전체)
-- l2: 소분류 (genre에 '>'가 없으면 NULL)
ALTER TABLE books ADD COLUMN IF NOT EXISTS l1 TEXT
  GENERATED ALWAYS AS (
    split_part(COALESCE(genre, ''), '>', 1)
  ) STORED;

ALTER TABLE books ADD COLUMN IF NOT EXISTS l2 TEXT
  GENERATED ALWAYS AS (
    NULLIF(split_part(COALESCE(genre, ''), '>', 2), '')
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_books_l1_l2 ON books (l1, l2) WHERE l1 IS NOT NULL;
