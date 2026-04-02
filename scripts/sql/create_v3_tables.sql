-- v3 추천 엔진용 테이블
-- 실행: Supabase Dashboard > SQL Editor

-- 1. 고유 장르 임베딩 (~320행)
CREATE TABLE IF NOT EXISTS genre_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  genre_text TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('l1', 'l2')),
  embedding vector(2000) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(genre_text, level)
);

-- 2. 책별 v3 벡터 (desc + L1/L2 FK)
CREATE TABLE IF NOT EXISTS book_v3_vectors (
  book_id UUID PRIMARY KEY REFERENCES books(id),
  desc_embedding vector(2000),
  source_text TEXT,
  l1_text TEXT,
  l2_text TEXT,
  l1_genre_id UUID REFERENCES genre_embeddings(id),
  l2_genre_id UUID REFERENCES genre_embeddings(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_book_v3_l1 ON book_v3_vectors(l1_genre_id);
CREATE INDEX IF NOT EXISTS idx_book_v3_l2 ON book_v3_vectors(l2_genre_id);
