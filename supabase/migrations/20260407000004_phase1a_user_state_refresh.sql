-- 20260407_phase1a_user_state_refresh.sql
BEGIN;

-- 갱신 함수: 모든 유저의 state 를 user_books / impressions 에서 재계산
CREATE OR REPLACE FUNCTION public.refresh_user_state()
RETURNS void AS $$
BEGIN
  INSERT INTO public.user_state (
    user_id, total_likes, total_saves, total_finished,
    consecutive_ignores, last_active_at, is_active, current_tier, updated_at
  )
  SELECT
    u.id AS user_id,
    COALESCE(ub.likes, 0) AS total_likes,
    COALESCE(ub.saves, 0) AS total_saves,
    COALESCE(ub.finished, 0) AS total_finished,
    0 AS consecutive_ignores, -- Phase 1B 에서 impressions 로 계산
    ub.last_active_at,
    (ub.last_active_at IS NOT NULL AND ub.last_active_at > NOW() - INTERVAL '30 days') AS is_active,
    CASE
      WHEN COALESCE(ub.likes, 0) >= 6 THEN 2
      WHEN COALESCE(ub.likes, 0) >= 3 THEN 1
      ELSE 0
    END AS current_tier,
    NOW() AS updated_at
  FROM public.users u
  LEFT JOIN (
    SELECT
      user_id,
      COUNT(*) FILTER (WHERE rating = 'good')              AS likes,
      COUNT(*) FILTER (WHERE status = 'wishlist')          AS saves,
      COUNT(*) FILTER (WHERE status = 'finished')          AS finished,
      MAX(updated_at)                                       AS last_active_at
    FROM public.user_books
    GROUP BY user_id
  ) ub ON ub.user_id = u.id
  ON CONFLICT (user_id) DO UPDATE SET
    total_likes = EXCLUDED.total_likes,
    total_saves = EXCLUDED.total_saves,
    total_finished = EXCLUDED.total_finished,
    last_active_at = EXCLUDED.last_active_at,
    is_active = EXCLUDED.is_active,
    current_tier = EXCLUDED.current_tier,
    updated_at = NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- pg_cron extension (이미 켜져있으면 무시)
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- 기존 동일 job 제거 후 등록
SELECT cron.unschedule(jobid)
  FROM cron.job
  WHERE jobname = 'refresh_user_state_hourly';

SELECT cron.schedule(
  'refresh_user_state_hourly',
  '0 * * * *',
  $$SELECT public.refresh_user_state();$$
);

COMMIT;
