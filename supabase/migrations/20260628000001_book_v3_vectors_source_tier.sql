-- book_v3_vectors.source_tier: 임베딩 소스 품질 등급.
--   rich       = rich_description >= 200자 (clean_html 후)
--   kakao_desc = 카카오 description (한 문단 줄거리)
--   minimal    = title+author+genre (최후 폴백)
-- 스코어러가 tier 로 후보를 차등 down-weight (rich 우선, 빈약은 폴백).
ALTER TABLE book_v3_vectors
  ADD COLUMN IF NOT EXISTS source_tier TEXT NOT NULL DEFAULT 'rich';

ALTER TABLE book_v3_vectors
  DROP CONSTRAINT IF EXISTS book_v3_vectors_source_tier_check;
ALTER TABLE book_v3_vectors
  ADD CONSTRAINT book_v3_vectors_source_tier_check
  CHECK (source_tier IN ('rich', 'kakao_desc', 'minimal'));

-- 기존 provisional thin 행 backfill. C1 ensure_books_embedded 가 이미 prod 라이브라
-- provisional=TRUE thin 행이 존재 → 블랭킷 DEFAULT 'rich' 면 오라벨되어 감점 0 +
-- reembed 영구 skip(리뷰 R2 BLOCKER). provisional 비트만으론 kakao_desc/minimal 구분
-- 불가 → 'kakao_desc' 임시 라벨, reembed_provisional 이 build_desc_source 재도출로 교정.
UPDATE book_v3_vectors SET source_tier = 'kakao_desc' WHERE provisional = TRUE;
