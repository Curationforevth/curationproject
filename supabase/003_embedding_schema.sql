-- =============================================
-- 003: 수집 & 임베딩 파이프라인 스키마 확장
-- Spec: docs/superpowers/specs/2026-03-20-batch-collection-strategy-design.md
-- =============================================

-- 1. books 테이블 확장
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS sales_point INT;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS enriched_description TEXT;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- books에도 updated_at 자동 갱신 트리거 (기존 handle_updated_at 함수 재사용)
CREATE TRIGGER on_books_updated
  BEFORE UPDATE ON public.books
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- sales_point 인덱스 (Tier 2 강화 우선순위 조회용)
CREATE INDEX IF NOT EXISTS idx_books_sales_point ON public.books(sales_point DESC NULLS LAST);

-- 2. book_embeddings 테이블 확장
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS tier SMALLINT DEFAULT 1;
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE TRIGGER on_book_embeddings_updated
  BEFORE UPDATE ON public.book_embeddings
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- 3. HNSW 벡터 인덱스 (코사인 유사도 검색용)
CREATE INDEX IF NOT EXISTS idx_book_embeddings_hnsw
  ON public.book_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- 4. batch_collection_state에 auto updated_at 트리거 추가
CREATE TRIGGER on_batch_collection_state_updated
  BEFORE UPDATE ON public.batch_collection_state
  FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- 5. source 컬럼 CHECK 제약 업데이트 (kakao 추가 확인)
-- 기존 001에서 이미 ('kakao', 'aladin')으로 설정됨. 변경 불필요.
