-- Phase 1B — 04_book_co_occurrence
CREATE TABLE IF NOT EXISTS book_co_occurrence (
  book_a_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  book_b_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  co_like_count INT DEFAULT 0,
  co_save_count INT DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (book_a_id, book_b_id),
  CHECK (book_a_id < book_b_id)
);

CREATE INDEX IF NOT EXISTS idx_co_a
  ON book_co_occurrence (book_a_id, co_like_count DESC) WHERE co_like_count >= 3;
CREATE INDEX IF NOT EXISTS idx_co_b
  ON book_co_occurrence (book_b_id, co_like_count DESC) WHERE co_like_count >= 3;

ALTER TABLE book_co_occurrence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS co_read ON book_co_occurrence;
CREATE POLICY co_read ON book_co_occurrence FOR SELECT USING (TRUE);
