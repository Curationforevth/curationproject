-- book_embeddings.source_text 전문 → 해시로 대체 (읽는 곳이 없는 10.2MB 회수)
--
-- 배경(2026-08-27): Supabase Free DB 가 **499/500 MB (99.8%)**. reason 임베딩 백필로
-- 추천 커버리지를 34%→68% 로 올린 대가로 용량이 한계에 닿았다.
--
-- 왜 안전한가 — **이 컬럼은 순수 쓰기 전용이다.** book_embeddings 를 조회하는 모든
-- 코드가 다른 컬럼만 읽는다(전수 확인):
--     scripts/tier2_embedder.py:208   select("book_id, data_sources")
--     scripts/tier2_embedder.py:342/344 select("id", count="exact")
--     scripts/taste_recomputer.py:175 select("book_id, tier, embedding")
--     scripts/tier1_embedder.py:71    select("book_id")
-- recommendation-server 는 이 테이블 자체를 참조하지 않는다(v3 는 book_v3_vectors +
-- book_love_reasons 사용).
--
-- 버리지 않고 **해시로 남긴다** — "임베딩한 원문이 바뀌었는지" 판정 능력은 보존되고
-- (sha256 동등 ⟺ 원문 동등), 저장은 행당 ~1.8KB → 64B 로 줄어든다.
-- ※ 공간 실제 회수는 이 마이그레이션 뒤 VACUUM FULL 로 이뤄진다(트랜잭션 밖 작업).
--
-- ⚠️ book_v3_vectors.source_text 는 **건드리지 않는다** — 그쪽은 reembed_provisional 이
--    `new_text != stored_source_text` 로 실제 비교에 쓰고, 저장 경로마다 truncate 규칙이
--    달라(148행 [:2000] vs 245행 전문) 해시 전환 시 대량 재임베딩(OpenAI 비용) 위험이
--    있다. 별도 과제로 남긴다.

SET statement_timeout = 0;

ALTER TABLE book_embeddings
  ADD COLUMN IF NOT EXISTS source_text_sha text;

-- PG17 내장 sha256(bytea) 사용 — pgcrypto digest() 의존 없음.
UPDATE book_embeddings
   SET source_text_sha = encode(sha256(source_text::bytea), 'hex'),
       source_text = NULL
 WHERE source_text IS NOT NULL;

COMMENT ON COLUMN book_embeddings.source_text_sha IS
  '임베딩 원문의 sha256. 전문은 읽는 곳이 없어 2026-08-27 에 해시로 대체(용량 회수). 변경 감지는 해시 비교로 가능.';
