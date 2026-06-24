-- Phase 1B — 14_functions_user_state
-- refresh_user_top_taste_single: 단일 유저 top_authors/top_l1s 갱신
CREATE OR REPLACE FUNCTION refresh_user_top_taste_single(target_user_id UUID) RETURNS void AS $$
BEGIN
  UPDATE user_state SET
    top_authors = COALESCE((
      SELECT jsonb_agg(jsonb_build_object('author', author, 'count', cnt))
      FROM (
        SELECT b.author, COUNT(*) as cnt
        FROM user_books ub JOIN books b ON ub.book_id = b.id
        WHERE ub.user_id = target_user_id AND ub.rating = 'good' AND b.author IS NOT NULL
        GROUP BY b.author
        ORDER BY cnt DESC
        LIMIT 5
      ) top
    ), '[]'::jsonb),
    top_l1s = COALESCE((
      SELECT jsonb_agg(jsonb_build_object('l1', l1, 'count', cnt))
      FROM (
        SELECT b.l1, COUNT(*) as cnt
        FROM user_books ub JOIN books b ON ub.book_id = b.id
        WHERE ub.user_id = target_user_id AND ub.rating = 'good' AND b.l1 IS NOT NULL
        GROUP BY b.l1
        ORDER BY cnt DESC
        LIMIT 3
      ) top
    ), '[]'::jsonb),
    updated_at = NOW()
  WHERE user_id = target_user_id;
END;
$$ LANGUAGE plpgsql;

-- refresh_user_top_taste_all: daily safety net
CREATE OR REPLACE FUNCTION refresh_user_top_taste_all() RETURNS void AS $$
DECLARE rec RECORD;
BEGIN
  FOR rec IN SELECT user_id FROM user_state WHERE is_active = TRUE LOOP
    PERFORM refresh_user_top_taste_single(rec.user_id);
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- refresh_user_state_single: Phase 1A 함수를 재정의 (top_taste 호출 포함)
-- (기존 hourly pg_cron refresh_user_state는 그대로 유지)
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
    v_last > NOW() - INTERVAL '30 days', v_tier, NOW()
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

-- trigger_refresh_user_state: user_books 변경 시 호출
CREATE OR REPLACE FUNCTION trigger_refresh_user_state() RETURNS trigger AS $$
BEGIN
  PERFORM refresh_user_state_single(COALESCE(NEW.user_id, OLD.user_id));
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_books_state_sync ON user_books;
CREATE TRIGGER user_books_state_sync
  AFTER INSERT OR UPDATE OR DELETE ON user_books
  FOR EACH ROW EXECUTE FUNCTION trigger_refresh_user_state();
