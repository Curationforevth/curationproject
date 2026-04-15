-- Phase 1B — 15_functions_stage
CREATE OR REPLACE FUNCTION check_stage_transition() RETURNS void AS $$
DECLARE
  v_stage INT; v_entered TIMESTAMPTZ; v_cfg JSONB; v_pre_ctr FLOAT;
  v_users INT; v_pairs INT;
  v_next_stage INT; v_next_key TEXT;
  v_post_ctr FLOAT; v_exposure INT;
BEGIN
  SELECT current_stage, entered_at, thresholds, pre_transition_ctr
  INTO v_stage, v_entered, v_cfg, v_pre_ctr
  FROM recommendation_stage WHERE id = 1;

  SELECT COUNT(*) INTO v_users FROM user_state WHERE is_active = TRUE;
  SELECT COUNT(*) INTO v_pairs FROM book_co_occurrence WHERE co_like_count >= 3;

  -- Promote 시도 (7일 경과 + 임계 충족)
  v_next_stage := v_stage + 1;
  v_next_key := 'stage' || v_next_stage;

  IF v_cfg ? v_next_key AND v_entered < NOW() - INTERVAL '7 days' THEN
    IF v_users >= (v_cfg->v_next_key->>'users')::INT
       AND v_pairs >= (v_cfg->v_next_key->>'pairs')::INT THEN
      SELECT (COUNT(*) FILTER (WHERE action IN ('clicked','liked','saved'))::float
              / NULLIF(COUNT(*), 0))
      INTO v_pre_ctr
      FROM recommendation_impressions WHERE shown_at >= v_entered;

      UPDATE recommendation_stage
        SET current_stage = v_next_stage,
            entered_at = NOW(),
            pre_transition_ctr = v_pre_ctr,
            post_transition_ctr = NULL,
            updated_at = NOW()
        WHERE id = 1;
      INSERT INTO stage_transitions (from_stage, to_stage, reason,
        active_user_count, co_pair_count, pre_ctr)
        VALUES (v_stage, v_next_stage, 'auto_promote', v_users, v_pairs, v_pre_ctr);
      RETURN;
    END IF;
  END IF;

  -- Rollback 체크 (stage > 0 + 7일 경과 + pre_ctr 존재 + 최소 노출 + CTR -20% 이상 하락)
  IF v_stage > 0
     AND v_entered < NOW() - INTERVAL '7 days'
     AND v_pre_ctr IS NOT NULL THEN
    SELECT COUNT(*) INTO v_exposure
    FROM recommendation_impressions WHERE shown_at >= v_entered;

    IF v_exposure >= (v_cfg->>'min_exposure')::INT THEN
      SELECT (COUNT(*) FILTER (WHERE action IN ('clicked','liked','saved'))::float
              / NULLIF(COUNT(*), 0))
      INTO v_post_ctr
      FROM recommendation_impressions WHERE shown_at >= v_entered;

      IF v_pre_ctr > 0
         AND (v_post_ctr - v_pre_ctr) / v_pre_ctr < (v_cfg->>'rollback_threshold')::FLOAT THEN
        UPDATE recommendation_stage
          SET current_stage = v_stage - 1,
              entered_at = NOW(),
              pre_transition_ctr = NULL,
              post_transition_ctr = v_post_ctr,
              updated_at = NOW()
          WHERE id = 1;
        INSERT INTO stage_transitions (from_stage, to_stage, reason, pre_ctr, post_ctr)
          VALUES (v_stage, v_stage - 1, 'auto_rollback', v_pre_ctr, v_post_ctr);
      END IF;
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql;
