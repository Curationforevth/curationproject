-- Phase 1B — 06_recommendation_stage
CREATE TABLE IF NOT EXISTS recommendation_stage (
  id INT PRIMARY KEY CHECK (id = 1),
  current_stage INT DEFAULT 0 CHECK (current_stage IN (0,1,2,3)),
  entered_at TIMESTAMPTZ DEFAULT NOW(),
  thresholds JSONB DEFAULT '{
    "stage1": {"users": 100, "pairs": 200, "cf_weight": 0.2},
    "stage2": {"users": 300, "pairs": 1000, "cf_weight": 0.4},
    "stage3": {"users": 500, "pairs": 3000, "cf_weight": 0.6},
    "rollback_threshold": -0.2,
    "min_exposure": 1000
  }'::jsonb,
  pre_transition_ctr FLOAT,
  post_transition_ctr FLOAT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO recommendation_stage (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS stage_transitions (
  id BIGSERIAL PRIMARY KEY,
  from_stage INT NOT NULL,
  to_stage INT NOT NULL,
  reason TEXT CHECK (reason IN ('auto_promote','auto_rollback','manual')),
  active_user_count INT,
  co_pair_count INT,
  pre_ctr FLOAT,
  post_ctr FLOAT,
  transitioned_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE recommendation_stage ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS stage_read ON recommendation_stage;
CREATE POLICY stage_read ON recommendation_stage FOR SELECT USING (TRUE);

ALTER TABLE stage_transitions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS transitions_read ON stage_transitions;
CREATE POLICY transitions_read ON stage_transitions FOR SELECT USING (TRUE);
