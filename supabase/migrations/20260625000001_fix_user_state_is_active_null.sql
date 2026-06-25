-- user_state 트리거 버그 수정.
--
-- refresh_user_state_single 은 user_books 변경 트리거(user_books_state_sync)에서
-- 호출된다. 유저의 마지막 user_books 행이 삭제되면 집계 SELECT 의
-- MAX(updated_at) → v_last 가 NULL → is_active = (NULL > NOW()-30d) = NULL →
-- user_state.is_active(NOT NULL) 위반(SQLSTATE 23502) → user_books DELETE 자체가
-- 실패한다(실유저가 좋아요를 전부 지울 때 차단됨).
--
-- COALESCE 로 NULL → FALSE 보정. 함수 시그니처/로직은 동일, is_active 한 줄만 수정.
CREATE OR REPLACE FUNCTION refresh_user_state_single(target_user_id UUID) RETURNS void AS $$
DECLARE
  v_likes INT; v_saves INT; v_finished INT; v_last TIMESTAMPTZ; v_tier INT;
BEGIN
  SELECT
    COUNT(*) FILTER (WHERE rating = 'good'),
    COUNT(*) FILTER (WHERE status = 'wishlist'),
    COUNT(*) FILTER (WHERE status = 'finished'),
    MAX(updated_at)
  INTO v_likes, v_saves, v_finished, v_last
  FROM user_books WHERE user_id = target_user_id;

  v_tier := CASE
    WHEN v_likes < 3 THEN 0
    WHEN v_likes < 6 THEN 1
    ELSE 2
  END;

  INSERT INTO user_state (
    user_id, total_likes, total_saves, total_finished,
    last_active_at, is_active, current_tier, updated_at
  ) VALUES (
    target_user_id, v_likes, v_saves, v_finished, v_last,
    COALESCE(v_last > NOW() - INTERVAL '30 days', FALSE), v_tier, NOW()
  ) ON CONFLICT (user_id) DO UPDATE SET
    total_likes = EXCLUDED.total_likes,
    total_saves = EXCLUDED.total_saves,
    total_finished = EXCLUDED.total_finished,
    last_active_at = EXCLUDED.last_active_at,
    is_active = EXCLUDED.is_active,
    current_tier = EXCLUDED.current_tier,
    updated_at = NOW();

  PERFORM refresh_user_top_taste_single(target_user_id);
END;
$$ LANGUAGE plpgsql;
