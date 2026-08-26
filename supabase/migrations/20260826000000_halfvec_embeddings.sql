-- 임베딩 컬럼 vector(f32) → halfvec(f16) 전환 — DB 용량 감축
--
-- 배경(2026-08-26): Supabase Free 플랜 Database Size **0.699 / 0.5 GB (140%)**.
-- 유예기간은 2026-04-15 에 종료됐고 Fair Use Policy 상 프로젝트가 제한되면
-- 요청이 402 로 떨어진다. 초과 항목은 DB 크기 하나뿐(egress 9%, MAU 3, storage 0%).
--
-- 원인 분석(실측):
--   * 공간의 대부분이 TOAST(559MB)이고, 그중 압도적 1위가
--     book_love_reasons.reason_embedding = 8,004B × 80,792행 ≈ 617MB(논리).
--   * 부풀림 아님(논리 879MB > 실제 TOAST 559MB → 이미 압축됨) → VACUUM FULL 무의미.
--   * 미사용 인덱스 0, 드롭 가능한 유물 테이블 0(book_embeddings 는 tier1/tier2_embedder 가 사용).
--
-- 왜 halfvec 이 안전한가 — **서빙 정밀도가 이미 f16**:
--   recommendation-server/scripts/index_rebuild_direct.py 가 index.pkl 을 만들 때
--   `.astype(np.float16)` 로 저장한다(prestacked_reasons_f16 / desc_matrix_f16 /
--   agg_reason_matrix_f16). 추천 계산은 전적으로 이 pkl 로 이뤄지고, 프로덕션
--   서빙 경로에는 SQL 벡터 연산이 없다(<=> 를 쓰는 함수는 전부 v2 유물이며
--   .rpc() 호출처 없음, 벡터 인덱스 스캔 0회). 따라서 DB 를 f16 으로 낮춰도
--   **추천이 실제로 쓰는 정밀도와 동일**하다.
--
-- 프로덕션 리허설(BEGIN/ROLLBACK) 실측:
--   book_embeddings 126.0MB → 64.1MB (**49.1% 절감**), 20.5초.
--   텍스트 배열 INSERT → halfvec 캐스팅 정상, ::text 읽기 형식 동일(빌드 경로 무영향),
--   참조 함수가 ALTER 를 막지 않음.
--
-- ⚠️ statement_timeout: DB 기본 2min / service_role 60s. 305MB 테이블 재작성은
--    그보다 오래 걸려 그대로 두면 마이그레이션이 중간에 죽는다 → 아래에서 해제.
--
-- ⚠️ 교차 타입 방지: pgvector 에는 `halfvec <=> vector` 연산자가 없다. 일부 v2 함수가
--    book_* 컬럼과 user_taste_* 컬럼을 함께 비교하므로, 둘 중 하나만 바꾸면 그 함수들이
--    런타임에 깨진다. user_taste_* 는 0행이라 변환 비용이 사실상 0 → **전부 함께 전환**해
--    타입을 일관되게 유지한다(해당 hnsw 인덱스는 halfvec 연산자 클래스로 재생성).

SET statement_timeout = 0;

-- 작은 것부터 — 중간 실패 시 진행분을 최대한 남긴다.
ALTER TABLE genre_embeddings
  ALTER COLUMN embedding TYPE halfvec(2000) USING embedding::halfvec(2000);

-- user_taste_* (0행): hnsw 인덱스가 vector_cosine_ops 에 묶여 있어 먼저 드롭.
DROP INDEX IF EXISTS idx_user_taste_vectors_hnsw;
DROP INDEX IF EXISTS idx_utr_embedding;

ALTER TABLE user_taste_vectors
  ALTER COLUMN vector TYPE halfvec(1536) USING vector::halfvec(1536);

ALTER TABLE user_taste_reasons
  ALTER COLUMN reason_embedding TYPE halfvec(2000) USING reason_embedding::halfvec(2000);

CREATE INDEX idx_user_taste_vectors_hnsw
  ON user_taste_vectors USING hnsw (vector halfvec_cosine_ops) WITH (m = 16);
CREATE INDEX idx_utr_embedding
  ON user_taste_reasons USING hnsw (reason_embedding halfvec_cosine_ops);

-- 본진 3개 (합계 약 557MB → 약 290MB 예상)
ALTER TABLE book_embeddings
  ALTER COLUMN embedding TYPE halfvec(1536) USING embedding::halfvec(1536);

ALTER TABLE book_v3_vectors
  ALTER COLUMN desc_embedding TYPE halfvec(2000) USING desc_embedding::halfvec(2000);

ALTER TABLE book_love_reasons
  ALTER COLUMN reason_embedding TYPE halfvec(2000) USING reason_embedding::halfvec(2000);
