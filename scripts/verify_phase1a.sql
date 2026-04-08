-- scripts/verify_phase1a.sql
-- Phase 1A 인프라 동작 확인

-- 1. user_books 정규화
SELECT 'status' AS k, status AS v, COUNT(*) FROM public.user_books GROUP BY status
UNION ALL
SELECT 'rating', COALESCE(rating, '(null)'), COUNT(*) FROM public.user_books GROUP BY rating;

-- 2. history trigger 살아있는지
SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.user_books'::regclass;

-- 3. impressions 인덱스
SELECT indexname FROM pg_indexes WHERE tablename = 'recommendation_impressions';

-- 4. user_state 갱신 결과
SELECT current_tier, COUNT(*), SUM(total_likes) AS sum_likes
FROM public.user_state GROUP BY current_tier ORDER BY current_tier;

-- 5. cron job
SELECT jobname, schedule FROM cron.job WHERE jobname LIKE '%user_state%';
