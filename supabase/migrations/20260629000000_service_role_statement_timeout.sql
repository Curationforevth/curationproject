-- 인덱스 빌드가 book_v3_vectors(9483행 × 2000차원 임베딩 ≈ 235MB)를 읽을 때, 무료
-- Supabase 의 기본 statement_timeout(서비스롤 ~8s)을 초과해 57014(canceling statement
-- due to statement timeout)로 빌드 실패(2026-06-29, 3회). 백엔드/빌드 전용 service_role
-- 의 timeout 을 60s 로 올려 대용량 벡터 read 가 완료되게 한다. (Cloudflare ~100s 한도
-- 아래라 504/522 도 회피.) 서빙 쿼리는 작아 60s 에 근접하지 않으므로 안전.
ALTER ROLE service_role SET statement_timeout = '60s';
