-- recommendation_cache: 유저별 추천 결과 캐시
-- input_hash(SHA256) 기반 무효화 (TTL 없음)
-- computing 플래그: 동시 재계산 방지

CREATE TABLE IF NOT EXISTS recommendation_cache (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    recommendations JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    good_count INT NOT NULL DEFAULT 0,
    bad_count INT NOT NULL DEFAULT 0,
    has_feedback BOOLEAN NOT NULL DEFAULT false,
    input_hash TEXT NOT NULL,
    computing BOOLEAN NOT NULL DEFAULT false
);
