-- RLS 활성화: book_v3_vectors, genre_embeddings
-- 두 테이블은 서버 배치 + 추천 서버(build_index)만 접근하며,
-- 모두 SUPABASE_SERVICE_ROLE_KEY를 사용하므로 RLS를 우회한다.
-- Flutter 앱(anon/authenticated)은 직접 읽지 않으므로 별도 정책 불필요.
-- → RLS만 enable, 정책 없음 = service_role 외 모든 접근 차단.

ALTER TABLE public.book_v3_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.genre_embeddings ENABLE ROW LEVEL SECURITY;
