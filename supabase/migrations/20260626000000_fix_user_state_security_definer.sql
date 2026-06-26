-- user_state 트리거 RLS 회귀(regression) 수정.
--
-- refresh_user_state_single() 은 user_books 변경 트리거(user_books_state_sync →
-- trigger_refresh_user_state)에서 호출되어 user_state 를 UPSERT 한다.
-- 원래 Phase 1A(20260407000004)에선 SECURITY DEFINER 였으나, Phase 1B 재정의
-- (20260415000014) 와 is_active 수정(20260625000001) 이 CREATE OR REPLACE 하면서
-- SECURITY DEFINER 를 빠뜨렸다. 그 결과 트리거가 '인증된 유저' 권한으로 user_state 를
-- 쓰려 하는데, user_state 엔 SELECT 정책만 있고 INSERT/UPDATE 정책이 없어
-- "new row violates row-level security policy for table user_state" (SQLSTATE 42501) 로
-- 실패 → 트리거 실패가 원 트랜잭션을 롤백 → 책 추가/피드백/상태변경/서재정렬이 전부 막혔다.
--
-- 수정: SECURITY DEFINER 복원(owner=postgres 가 RLS 우회). 본문은 20260625000001 과
-- 동일(is_active COALESCE 유지). 중첩 호출 refresh_user_top_taste_single 도 DEFINER
-- 컨텍스트 안에서 실행되어 함께 해결된다.
-- (트리거/RLS 는 단위테스트 불가 — 실쓰기로만 검증.)
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
$$ LANGUAGE plpgsql SECURITY DEFINER;
