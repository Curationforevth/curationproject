-- book_v3_vectors.source_text 전문 → 해시로 대체 (13.2MB 회수)
--
-- 배경(2026-08-27): Supabase Free DB 가 한계(reason 백필로 추천 커버리지를
-- 34%→68% 로 올린 대가). book_embeddings 쪽 10.6MB 회수에 이은 두 번째.
--
-- 이 컬럼의 유일한 실제 용도는 **변경 감지 동등 비교**다:
--     scripts/reembed_provisional.py:52  "reembed": new_text != stored_source_text
-- 그래서 sha256 으로 바꿔도 의미가 보존된다(해시 동등 ⟺ 원문 동등).
--
-- ⚠️ 사전 확인한 것 — 절단 불일치가 없어야 안전하다:
--   generate_book_v3_vectors._pick_source_text 가 이미 [:2000] 으로 잘라 반환하고
--   (48/51/54행), reembed/서버도 같은 헬퍼 정책을 쓴다. 실측으로 전 행 max(length)
--   = 2000, 2000 초과 0행 → 양쪽 저장값이 동일하다. 따라서 해시 비교로 바꿔도
--   대량 재임베딩(OpenAI 비용)이 유발되지 않는다.
--   추가로 reembed 는 source_tier != 'rich' 행만 보는데, 그 4,590행 중 정확히
--   2000자인 행은 0개다(절단 자체가 발생하지 않는 구간).
--
-- 쓰기 경로 3곳을 같은 PR 에서 해시 기록으로 전환한다:
--   scripts/generate_book_v3_vectors.py, scripts/reembed_provisional.py,
--   recommendation-server/engine/user_embed.py
--
-- ※ 공간 실제 회수는 이 마이그레이션 뒤 VACUUM FULL 로 이뤄진다(트랜잭션 밖).

SET statement_timeout = 0;

ALTER TABLE book_v3_vectors
  ADD COLUMN IF NOT EXISTS source_text_sha text;

-- PG17 내장 sha256(bytea) — pgcrypto 의존 없음.
UPDATE book_v3_vectors
   SET source_text_sha = encode(sha256(source_text::bytea), 'hex'),
       source_text = NULL
 WHERE source_text IS NOT NULL;

COMMENT ON COLUMN book_v3_vectors.source_text_sha IS
  '임베딩 원문(_pick_source_text 결과, [:2000])의 sha256. 전문은 변경 감지 비교에만 쓰여 2026-08-27 에 해시로 대체(용량 회수).';
