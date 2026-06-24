CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER TABLE books ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
DROP TRIGGER IF EXISTS books_updated_at ON books;
CREATE TRIGGER books_updated_at
    BEFORE UPDATE ON books FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE book_v3_vectors ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
DROP TRIGGER IF EXISTS book_v3_vectors_updated_at ON book_v3_vectors;
CREATE TRIGGER book_v3_vectors_updated_at
    BEFORE UPDATE ON book_v3_vectors FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE book_love_reasons ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
DROP TRIGGER IF EXISTS book_love_reasons_updated_at ON book_love_reasons;
CREATE TRIGGER book_love_reasons_updated_at
    BEFORE UPDATE ON book_love_reasons FOR EACH ROW EXECUTE FUNCTION set_updated_at();
