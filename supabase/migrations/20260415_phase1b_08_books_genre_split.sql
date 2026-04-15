-- Phase 1B — 08_books_genre_split
-- books.genre = "대분류>소분류". generated column으로 l1/l2 분리.
ALTER TABLE books ADD COLUMN IF NOT EXISTS l1 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL OR position('>' IN genre) = 0 THEN genre
         ELSE split_part(genre, '>', 1) END
  ) STORED;

ALTER TABLE books ADD COLUMN IF NOT EXISTS l2 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL OR position('>' IN genre) = 0 THEN NULL
         ELSE NULLIF(split_part(genre, '>', 2), '') END
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_books_l1_l2 ON books (l1, l2) WHERE l1 IS NOT NULL;
