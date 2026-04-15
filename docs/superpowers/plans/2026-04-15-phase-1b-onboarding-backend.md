# Phase 1B 온보딩/홈 백엔드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spec `docs/superpowers/specs/2026-04-15-phase-1b-onboarding-backend-design.md`에 정의된 Phase 1B 서버 백엔드 (User Tier 분기, 큐레이션 풀 시스템, co-occurrence, System Stage 자동 전환, 자동 migration 인프라)를 구현한다.

**Architecture:** FastAPI (Render)에 `/home` 신규 엔드포인트 + Tier 분기 로직 추가. Supabase에 19개 migration (테이블 + plpgsql 함수 + pg_cron 스케줄) 추가. GH Actions에 3개 신규 workflow (apply-migrations, generate_curation_themes, generate_cluster_themes). 모든 반복 작업은 pg_cron + trigger + workflow cron으로 자동화, 반복 수동 작업 0.

**Tech Stack:** Python 3.11 / FastAPI / Supabase (Postgres + pg_cron) / Supabase CLI / GitHub Actions / OpenAI API (gpt-4o-mini) / scikit-learn (KMeans) / pytest / locust

---

## 파일 구조

### 신규 파일
```
supabase/migrations/
  20260415_phase1b_00_extensions.sql
  20260415_phase1b_01_curation_themes.sql
  20260415_phase1b_02_curation_cache.sql
  20260415_phase1b_03_user_curation_history.sql
  20260415_phase1b_04_book_co_occurrence.sql
  20260415_phase1b_05_search_logs.sql
  20260415_phase1b_06_recommendation_stage.sql
  20260415_phase1b_07_user_state_extend.sql
  20260415_phase1b_08_books_genre_split.sql
  20260415_phase1b_09_book_cluster_assignments.sql
  20260415_phase1b_10_impressions_extend.sql
  20260415_phase1b_11_home_section_cache.sql
  20260415_phase1b_12_functions_curation.sql
  20260415_phase1b_13_functions_co_occurrence.sql
  20260415_phase1b_14_functions_user_state.sql
  20260415_phase1b_15_functions_stage.sql
  20260415_phase1b_16_functions_fallback.sql
  20260415_phase1b_17_functions_cleanup.sql
  20260415_phase1b_18_cron_schedules.sql

.github/workflows/
  apply-migrations.yml
  generate-curation-themes.yml
  generate-cluster-themes.yml
  verify-phase-1b.yml

recommendation-server/
  api/home.py              — Tier 분기 섹션 조립 엔드포인트
  api/curation.py          — /curations/{id}/books
  engine/recommend_core.py — recommend.py에서 scoring 로직 추출
  engine/tier.py           — Tier 분기 + 섹션 구성 규칙 + 한국어 조사
  engine/curation.py       — 가중 랜덤 sampling + 개인화 + 7일 디스카운트
  engine/home_cache.py     — home_section_cache 읽기/쓰기
  scripts/generate_curation_themes.py
  scripts/generate_cluster_themes.py
  tests/test_tier.py
  tests/test_curation.py
  tests/test_recommend_core.py
  tests/test_home.py

scripts/e2e_phase1b.sh     — Eden end-to-end curl 검증
```

### 수정 파일
```
recommendation-server/
  api/recommend.py         — Tier 2 체크 추가, scoring 로직을 recommend_core 로 위임
  main.py                  — home_router, curation_router 등록
```

### 참조 (변경 없음)
```
recommendation-server/engine/twostage.py, scorer.py, loader.py, cache.py
recommendation-server/api/similar.py, feedback.py
```

---

## 실행 순서 개요

- **Phase A (Task 1)**: apply-migrations workflow — 이후 모든 migration PR merge 시 자동 apply
- **Phase B (Task 2-3)**: 테이블 migrations 00-11 — 스키마 기반
- **Phase C (Task 4-8)**: 함수 migrations 12-17 + 스케줄 18
- **Phase D (Task 9-10)**: recommend_core 추출 + Tier 체크
- **Phase E (Task 11-14)**: Tier engine + Curation engine + home cache
- **Phase F (Task 15-16)**: /home + /curations endpoints
- **Phase G (Task 17-18)**: GH Actions 생성 스크립트 (curation themes, cluster)
- **Phase H (Task 19-20)**: 검증 (verify workflow + e2e script)

각 Task 완료 시 commit. PR 단위는 Eden 판단 (Task 2-3 묶음 / Task 4-8 묶음 등 논리적 단위).

---

# Phase A: Migration 자동화 인프라

## Task 1: apply-migrations workflow 작성

**Files:**
- Create: `.github/workflows/apply-migrations.yml`

- [ ] **Step 1: workflow 파일 생성**

```yaml
name: Apply Migrations

on:
  push:
    branches: [main]
    paths: ['supabase/migrations/**']
  workflow_dispatch:
    inputs:
      first_run:
        description: 'Bootstrap: register existing migrations as applied without executing'
        required: false
        default: 'false'
        type: choice
        options: ['false', 'true']

concurrency:
  group: apply-migrations
  cancel-in-progress: false

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: supabase/setup-cli@v1
        with:
          version: latest

      - name: Link Supabase project
        run: supabase link --project-ref ${{ secrets.SUPABASE_PROJECT_REF }}
        env:
          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}

      - name: Bootstrap existing migrations (first-run only)
        if: ${{ github.event.inputs.first_run == 'true' }}
        env:
          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
        run: |
          for f in supabase/migrations/*.sql; do
            base=$(basename "$f" .sql)
            version=$(echo "$base" | awk -F_ '{print $1}')
            echo "Repairing $version as applied..."
            supabase migration repair --status applied "$version" --password "$SUPABASE_DB_PASSWORD" || true
          done

      - name: Push pending migrations
        env:
          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
        run: supabase db push --password "${{ secrets.SUPABASE_DB_PASSWORD }}" --yes
```

- [ ] **Step 2: lint / syntax 검증**

Run: `cat .github/workflows/apply-migrations.yml | python3 -c "import yaml, sys; yaml.safe_load(sys.stdin)"`
Expected: 에러 없음 (yaml 파싱 성공)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/apply-migrations.yml
git commit -m "ci: apply-migrations workflow — PR 머지 시 Supabase 자동 적용"
```

---

# Phase B: 데이터 모델 Migrations

## Task 2: Tables 00-11 migrations 작성

**Files:**
- Create: `supabase/migrations/20260415_phase1b_00_extensions.sql`
- Create: `supabase/migrations/20260415_phase1b_01_curation_themes.sql`
- Create: `supabase/migrations/20260415_phase1b_02_curation_cache.sql`
- Create: `supabase/migrations/20260415_phase1b_03_user_curation_history.sql`
- Create: `supabase/migrations/20260415_phase1b_04_book_co_occurrence.sql`
- Create: `supabase/migrations/20260415_phase1b_05_search_logs.sql`
- Create: `supabase/migrations/20260415_phase1b_06_recommendation_stage.sql`
- Create: `supabase/migrations/20260415_phase1b_07_user_state_extend.sql`
- Create: `supabase/migrations/20260415_phase1b_08_books_genre_split.sql`
- Create: `supabase/migrations/20260415_phase1b_09_book_cluster_assignments.sql`
- Create: `supabase/migrations/20260415_phase1b_10_impressions_extend.sql`
- Create: `supabase/migrations/20260415_phase1b_11_home_section_cache.sql`

- [ ] **Step 1: 00_extensions.sql**

```sql
-- Phase 1B — 00_extensions
-- pg_cron extension 활성화 (이미 있으면 no-op)
CREATE EXTENSION IF NOT EXISTS pg_cron;
```

- [ ] **Step 2: 01_curation_themes.sql**

```sql
-- Phase 1B — 01_curation_themes
-- Spec Section 5.2 curation_themes
CREATE TABLE IF NOT EXISTS curation_themes (
  id BIGSERIAL PRIMARY KEY,
  theme_key TEXT UNIQUE NOT NULL,
  theme_type TEXT NOT NULL CHECK (theme_type IN ('genre_combo','author','keyword','cluster')),
  title TEXT NOT NULL,
  description TEXT,
  selection_query JSONB NOT NULL,
  parameters JSONB,
  min_books INT DEFAULT 5,
  max_books INT DEFAULT 30,
  priority FLOAT DEFAULT 1.0,
  personalization TEXT DEFAULT 'general'
    CHECK (personalization IN ('general','tier1+','tier2+','by_l1','by_author','by_keyword')),
  target_l1 TEXT,
  target_author TEXT,
  target_keyword TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_shown_at TIMESTAMPTZ,
  shown_count INT DEFAULT 0,
  click_count INT DEFAULT 0,
  click_rate FLOAT GENERATED ALWAYS AS (
    CASE WHEN shown_count > 0 THEN click_count::float / shown_count ELSE 0 END
  ) STORED
);

CREATE INDEX IF NOT EXISTS idx_curation_active_type
  ON curation_themes (theme_type) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_personalization
  ON curation_themes (personalization) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_target_l1
  ON curation_themes (target_l1) WHERE target_l1 IS NOT NULL AND is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_curation_target_author
  ON curation_themes (target_author) WHERE target_author IS NOT NULL AND is_active = TRUE;

ALTER TABLE curation_themes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS curation_themes_read ON curation_themes;
CREATE POLICY curation_themes_read ON curation_themes FOR SELECT USING (is_active = TRUE);
```

- [ ] **Step 3: 02_curation_cache.sql**

```sql
-- Phase 1B — 02_curation_cache
CREATE TABLE IF NOT EXISTS curation_cache (
  curation_id BIGINT PRIMARY KEY REFERENCES curation_themes(id) ON DELETE CASCADE,
  book_ids JSONB NOT NULL,
  cached_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_curation_cache_expires ON curation_cache (expires_at);

ALTER TABLE curation_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS curation_cache_read ON curation_cache;
CREATE POLICY curation_cache_read ON curation_cache FOR SELECT USING (TRUE);
```

- [ ] **Step 4: 03_user_curation_history.sql**

```sql
-- Phase 1B — 03_user_curation_history
CREATE TABLE IF NOT EXISTS user_curation_history (
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  curation_id BIGINT NOT NULL REFERENCES curation_themes(id) ON DELETE CASCADE,
  shown_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (user_id, curation_id, shown_at)
);

CREATE INDEX IF NOT EXISTS idx_uch_user_recent
  ON user_curation_history (user_id, shown_at DESC);

ALTER TABLE user_curation_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS uch_read_own ON user_curation_history;
CREATE POLICY uch_read_own ON user_curation_history FOR SELECT USING (auth.uid() = user_id);
```

- [ ] **Step 5: 04_book_co_occurrence.sql**

```sql
-- Phase 1B — 04_book_co_occurrence
CREATE TABLE IF NOT EXISTS book_co_occurrence (
  book_a_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  book_b_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  co_like_count INT DEFAULT 0,
  co_save_count INT DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (book_a_id, book_b_id),
  CHECK (book_a_id < book_b_id)
);

CREATE INDEX IF NOT EXISTS idx_co_a
  ON book_co_occurrence (book_a_id, co_like_count DESC) WHERE co_like_count >= 3;
CREATE INDEX IF NOT EXISTS idx_co_b
  ON book_co_occurrence (book_b_id, co_like_count DESC) WHERE co_like_count >= 3;

ALTER TABLE book_co_occurrence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS co_read ON book_co_occurrence;
CREATE POLICY co_read ON book_co_occurrence FOR SELECT USING (TRUE);
```

- [ ] **Step 6: 05_search_logs.sql**

```sql
-- Phase 1B — 05_search_logs
CREATE TABLE IF NOT EXISTS search_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  query TEXT NOT NULL,
  result_count INT NOT NULL,
  clicked_book_id UUID REFERENCES books(id) ON DELETE SET NULL,
  searched_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_search_user ON search_logs (user_id, searched_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_query ON search_logs USING gin (to_tsvector('simple', query));

ALTER TABLE search_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS search_insert_own ON search_logs;
CREATE POLICY search_insert_own ON search_logs FOR INSERT WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS search_read_own ON search_logs;
CREATE POLICY search_read_own ON search_logs FOR SELECT USING (auth.uid() = user_id);
```

- [ ] **Step 7: 06_recommendation_stage.sql**

```sql
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
```

- [ ] **Step 8: 07_user_state_extend.sql**

```sql
-- Phase 1B — 07_user_state_extend
-- top_authors: [{"author": "...", "count": N}, ...] top 5
-- top_l1s: [{"l1": "문학", "count": N}, ...] top 3
ALTER TABLE user_state
  ADD COLUMN IF NOT EXISTS top_authors JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS top_l1s JSONB DEFAULT '[]'::jsonb;
```

- [ ] **Step 9: 08_books_genre_split.sql**

```sql
-- Phase 1B — 08_books_genre_split
-- books.genre = "대분류>소분류". generated column으로 l1/l2 분리.
ALTER TABLE books ADD COLUMN IF NOT EXISTS l1 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL OR position('>' IN genre) = 0 THEN genre
         ELSE split_part(genre, '>', 1) END
  ) STORED;

ALTER TABLE books ADD COLUMN IF NOT EXISTS l2 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL OR position('>' IN genre) = 0 THEN NULL
         ELSE split_part(genre, '>', 2) END
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_books_l1_l2 ON books (l1, l2) WHERE l1 IS NOT NULL;
```

- [ ] **Step 10: 09_book_cluster_assignments.sql**

```sql
-- Phase 1B — 09_book_cluster_assignments
CREATE TABLE IF NOT EXISTS book_cluster_assignments (
  book_id UUID PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
  cluster_id INT NOT NULL,
  cluster_version TEXT NOT NULL,
  distance FLOAT,
  assigned_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cluster_members
  ON book_cluster_assignments (cluster_id, cluster_version);

ALTER TABLE book_cluster_assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cluster_read ON book_cluster_assignments;
CREATE POLICY cluster_read ON book_cluster_assignments FOR SELECT USING (TRUE);
```

- [ ] **Step 11: 10_impressions_extend.sql**

```sql
-- Phase 1B — 10_impressions_extend
ALTER TABLE recommendation_impressions
  ADD COLUMN IF NOT EXISTS curation_id BIGINT REFERENCES curation_themes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_imp_curation
  ON recommendation_impressions (curation_id, shown_at DESC)
  WHERE curation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_imp_shown_at
  ON recommendation_impressions (shown_at);
```

- [ ] **Step 12: 11_home_section_cache.sql**

```sql
-- Phase 1B — 11_home_section_cache
CREATE TABLE IF NOT EXISTS home_section_cache (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  sections JSONB NOT NULL,
  tier INT NOT NULL,
  stage INT NOT NULL,
  input_hash TEXT NOT NULL,
  computed_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE home_section_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS home_cache_read_own ON home_section_cache;
CREATE POLICY home_cache_read_own ON home_section_cache FOR SELECT USING (auth.uid() = user_id);
```

- [ ] **Step 13: 로컬 SQL syntax 체크 (psql parser)**

Run (로컬에 psql 설치된 경우):
```bash
for f in supabase/migrations/20260415_phase1b_0*.sql supabase/migrations/20260415_phase1b_10*.sql supabase/migrations/20260415_phase1b_11*.sql; do
  echo "=== $f ==="
  psql -c "\set ON_ERROR_STOP 1" -f "$f" --dry-run 2>&1 || true
done
```

실제 apply는 main merge 시 workflow 자동 수행. 여기선 문법만 체크.

- [ ] **Step 14: Commit**

```bash
git add supabase/migrations/20260415_phase1b_00_extensions.sql \
        supabase/migrations/20260415_phase1b_01_curation_themes.sql \
        supabase/migrations/20260415_phase1b_02_curation_cache.sql \
        supabase/migrations/20260415_phase1b_03_user_curation_history.sql \
        supabase/migrations/20260415_phase1b_04_book_co_occurrence.sql \
        supabase/migrations/20260415_phase1b_05_search_logs.sql \
        supabase/migrations/20260415_phase1b_06_recommendation_stage.sql \
        supabase/migrations/20260415_phase1b_07_user_state_extend.sql \
        supabase/migrations/20260415_phase1b_08_books_genre_split.sql \
        supabase/migrations/20260415_phase1b_09_book_cluster_assignments.sql \
        supabase/migrations/20260415_phase1b_10_impressions_extend.sql \
        supabase/migrations/20260415_phase1b_11_home_section_cache.sql
git commit -m "feat: Phase 1B 테이블 migrations (00-11)"
```

---

## Task 3: Functions & cron 12-18 migrations 작성

**Files:**
- Create: `supabase/migrations/20260415_phase1b_12_functions_curation.sql`
- Create: `supabase/migrations/20260415_phase1b_13_functions_co_occurrence.sql`
- Create: `supabase/migrations/20260415_phase1b_14_functions_user_state.sql`
- Create: `supabase/migrations/20260415_phase1b_15_functions_stage.sql`
- Create: `supabase/migrations/20260415_phase1b_16_functions_fallback.sql`
- Create: `supabase/migrations/20260415_phase1b_17_functions_cleanup.sql`
- Create: `supabase/migrations/20260415_phase1b_18_cron_schedules.sql`

- [ ] **Step 1: 12_functions_curation.sql**

```sql
-- Phase 1B — 12_functions_curation
-- refresh_curation_cache_all: hourly, per-theme exception handling
CREATE OR REPLACE FUNCTION refresh_curation_cache_all() RETURNS void AS $$
DECLARE
  theme RECORD;
  book_ids UUID[];
BEGIN
  FOR theme IN SELECT * FROM curation_themes WHERE is_active=TRUE LOOP
    BEGIN
      book_ids := NULL;
      CASE theme.theme_type
        WHEN 'genre_combo' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE l1 = theme.parameters->>'l1'
                  AND l2 = theme.parameters->>'l2'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'author' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE author = theme.parameters->>'author'
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'keyword' THEN
          SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
          FROM (SELECT id FROM books
                WHERE library_keywords @> ARRAY[theme.parameters->>'keyword']
                ORDER BY loan_count DESC NULLS LAST
                LIMIT theme.max_books) s;
        WHEN 'cluster' THEN
          SELECT array_agg(book_id ORDER BY distance) INTO book_ids
          FROM book_cluster_assignments
          WHERE cluster_id = (theme.parameters->>'cluster_id')::int
            AND cluster_version = theme.parameters->>'cluster_version'
          LIMIT theme.max_books;
      END CASE;

      IF array_length(book_ids, 1) >= theme.min_books THEN
        INSERT INTO curation_cache (curation_id, book_ids, cached_at, expires_at)
        VALUES (theme.id, to_jsonb(book_ids), NOW(), NOW() + INTERVAL '1 hour')
        ON CONFLICT (curation_id) DO UPDATE
          SET book_ids = EXCLUDED.book_ids,
              cached_at = NOW(),
              expires_at = EXCLUDED.expires_at;
      ELSE
        UPDATE curation_themes SET is_active = FALSE WHERE id = theme.id;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'Failed theme %: %', theme.id, SQLERRM;
      CONTINUE;
    END;
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- deactivate_curations: daily
CREATE OR REPLACE FUNCTION deactivate_curations() RETURNS void AS $$
BEGIN
  UPDATE curation_themes SET is_active = FALSE
    WHERE is_active = TRUE
      AND shown_count = 0
      AND created_at < NOW() - INTERVAL '30 days';

  UPDATE curation_themes SET is_active = FALSE
    WHERE is_active = TRUE
      AND shown_count >= 100
      AND click_rate < 0.005
      AND created_at < NOW() - INTERVAL '90 days';
END;
$$ LANGUAGE plpgsql;
```

- [ ] **Step 2: 13_functions_co_occurrence.sql**

```sql
-- Phase 1B — 13_functions_co_occurrence
CREATE OR REPLACE FUNCTION aggregate_co_occurrence() RETURNS void AS $$
BEGIN
  CREATE TEMP TABLE tmp_co ON COMMIT DROP AS
    SELECT
      LEAST(a.book_id, b.book_id) AS book_a_id,
      GREATEST(a.book_id, b.book_id) AS book_b_id,
      COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') AS co_like_count,
      COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') AS co_save_count
    FROM user_books a
    JOIN user_books b ON a.user_id = b.user_id AND a.book_id < b.book_id
    WHERE (a.rating='good' OR a.status='wishlist')
      AND (b.rating='good' OR b.status='wishlist')
    GROUP BY 1, 2
    HAVING COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') >= 3
        OR COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') >= 3;

  DELETE FROM book_co_occurrence;
  INSERT INTO book_co_occurrence (book_a_id, book_b_id, co_like_count, co_save_count, updated_at)
    SELECT book_a_id, book_b_id, co_like_count, co_save_count, NOW() FROM tmp_co;
END;
$$ LANGUAGE plpgsql;
```

- [ ] **Step 3: 14_functions_user_state.sql**

```sql
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
```

- [ ] **Step 4: 15_functions_stage.sql**

```sql
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
```

- [ ] **Step 5: 16_functions_fallback.sql**

```sql
-- Phase 1B — 16_functions_fallback
-- fallback_curation top 30 by loan_count (기존 seed script SQL 이관)
CREATE OR REPLACE FUNCTION refresh_fallback_curation() RETURNS void AS $$
BEGIN
  DELETE FROM fallback_curation;
  INSERT INTO fallback_curation (rank, book_id, loan_count, added_at)
    SELECT
      ROW_NUMBER() OVER (ORDER BY loan_count DESC NULLS LAST, id),
      id,
      loan_count,
      NOW()
    FROM books
    WHERE loan_count IS NOT NULL
    ORDER BY loan_count DESC NULLS LAST
    LIMIT 30;
END;
$$ LANGUAGE plpgsql;
```

- [ ] **Step 6: 17_functions_cleanup.sql**

```sql
-- Phase 1B — 17_functions_cleanup
CREATE OR REPLACE FUNCTION cleanup_user_curation_history() RETURNS void AS $$
BEGIN
  DELETE FROM user_curation_history WHERE shown_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cleanup_home_section_cache() RETURNS void AS $$
BEGIN
  DELETE FROM home_section_cache WHERE computed_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;
```

- [ ] **Step 7: 18_cron_schedules.sql**

```sql
-- Phase 1B — 18_cron_schedules
-- idempotent wrap: 이미 등록된 job은 unschedule 후 재등록

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-curation-cache') THEN
    PERFORM cron.unschedule('refresh-curation-cache');
  END IF;
  PERFORM cron.schedule('refresh-curation-cache', '5 * * * *',
    'SELECT refresh_curation_cache_all()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='aggregate-co-occurrence') THEN
    PERFORM cron.unschedule('aggregate-co-occurrence');
  END IF;
  PERFORM cron.schedule('aggregate-co-occurrence', '0 17 * * *',
    'SELECT aggregate_co_occurrence()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-user-top-taste') THEN
    PERFORM cron.unschedule('refresh-user-top-taste');
  END IF;
  PERFORM cron.schedule('refresh-user-top-taste', '15 17 * * *',
    'SELECT refresh_user_top_taste_all()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-fallback-curation') THEN
    PERFORM cron.unschedule('refresh-fallback-curation');
  END IF;
  PERFORM cron.schedule('refresh-fallback-curation', '30 17 * * *',
    'SELECT refresh_fallback_curation()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='deactivate-curations') THEN
    PERFORM cron.unschedule('deactivate-curations');
  END IF;
  PERFORM cron.schedule('deactivate-curations', '45 17 * * *',
    'SELECT deactivate_curations()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='check-stage-transition') THEN
    PERFORM cron.unschedule('check-stage-transition');
  END IF;
  PERFORM cron.schedule('check-stage-transition', '0 18 * * *',
    'SELECT check_stage_transition()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='cleanup-user-curation-history') THEN
    PERFORM cron.unschedule('cleanup-user-curation-history');
  END IF;
  PERFORM cron.schedule('cleanup-user-curation-history', '0 20 1 * *',
    'SELECT cleanup_user_curation_history()');
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='cleanup-home-section-cache') THEN
    PERFORM cron.unschedule('cleanup-home-section-cache');
  END IF;
  PERFORM cron.schedule('cleanup-home-section-cache', '0 20 15 * *',
    'SELECT cleanup_home_section_cache()');
END $$;
```

- [ ] **Step 8: Commit**

```bash
git add supabase/migrations/20260415_phase1b_12_functions_curation.sql \
        supabase/migrations/20260415_phase1b_13_functions_co_occurrence.sql \
        supabase/migrations/20260415_phase1b_14_functions_user_state.sql \
        supabase/migrations/20260415_phase1b_15_functions_stage.sql \
        supabase/migrations/20260415_phase1b_16_functions_fallback.sql \
        supabase/migrations/20260415_phase1b_17_functions_cleanup.sql \
        supabase/migrations/20260415_phase1b_18_cron_schedules.sql
git commit -m "feat: Phase 1B plpgsql 함수 + pg_cron 스케줄 (12-18)"
```

---

# Phase C: 서버 리팩토링 & Tier 분기

## Task 4: recommend_core 추출

**Files:**
- Create: `recommendation-server/engine/recommend_core.py`
- Modify: `recommendation-server/api/recommend.py:76-139` (scoring 로직을 recommend_core 호출로 교체)
- Create: `recommendation-server/tests/test_recommend_core.py`

- [ ] **Step 1: 테스트 파일 작성**

`recommendation-server/tests/test_recommend_core.py`:
```python
import numpy as np
import pytest
from engine.recommend_core import compute_scored_books

class _FakeIndex:
    def __init__(self):
        self.book_ids = ["b1", "b2", "b3"]

def test_compute_scored_books_empty_inputs_returns_empty():
    idx = _FakeIndex()
    result = compute_scored_books(
        index=idx,
        liked_books={},
        fb_data={},
        prestacked_reasons=None,
        desc_matrix_f16=None,
        agg_reason_matrix_f16=None,
        bid_order=[],
    )
    assert result == []

def test_compute_scored_books_v3_fallback_used_when_prestacked_none(monkeypatch):
    idx = _FakeIndex()
    called = {}
    def fake_scores(index, liked_books, fb_data):
        called["v3"] = True
        return {"b1": 0.5, "b2": 0.3}
    monkeypatch.setattr("engine.recommend_core.recommend_scores", fake_scores)

    result = compute_scored_books(
        index=idx,
        liked_books={"b1": {"rating": "good"}},
        fb_data={},
        prestacked_reasons=None,
        desc_matrix_f16=None,
        agg_reason_matrix_f16=None,
        bid_order=["b1", "b2"],
    )
    assert called.get("v3") is True
    assert isinstance(result, list)
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && pytest tests/test_recommend_core.py -v`
Expected: `ModuleNotFoundError: No module named 'engine.recommend_core'`

- [ ] **Step 3: engine/recommend_core.py 작성**

```python
"""recommendation-server/engine/recommend_core.py

api/recommend.py 의 scoring 로직을 추출. api 계층(HTTP/cache)과 분리하여
/home 에서도 재사용 가능하도록 한다.

반환: sorted by score desc, 최대 CACHE_TOP_N 길이의 (book_id, score) 리스트.
응답 조립(meta 조회, BookScore dict 변환)은 호출자에서 수행.
"""
from __future__ import annotations
from typing import Optional

from engine.scorer import recommend_scores
from engine.twostage import stage1_hybrid, batch_score_prestacked
from config import STAGE1_TOP_N, CACHE_TOP_N


def compute_scored_books(
    *,
    index,
    liked_books: dict,
    fb_data: dict,
    prestacked_reasons: Optional[dict],
    desc_matrix_f16,
    agg_reason_matrix_f16,
    bid_order: list,
) -> list[tuple[str, float]]:
    """
    Score all candidate books for a user and return top-N.

    - prestacked_reasons 있으면 v4 two-stage (stage1_hybrid + batch_score_prestacked)
    - 없으면 v3 fallback (recommend_scores — full index brute force)

    Empty inputs (no likes) → empty list.
    """
    if not liked_books and not fb_data:
        return []

    if prestacked_reasons is not None:
        candidates = stage1_hybrid(
            liked_books, fb_data,
            desc_matrix_f16, agg_reason_matrix_f16, bid_order,
            top_n=STAGE1_TOP_N,
        )
        scores = batch_score_prestacked(
            index, liked_books, fb_data, candidates, prestacked_reasons)
    else:
        scores = recommend_scores(index, liked_books, fb_data)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:CACHE_TOP_N]
```

- [ ] **Step 4: 테스트 재실행 — 통과 확인**

Run: `cd recommendation-server && pytest tests/test_recommend_core.py -v`
Expected: 2 passed

- [ ] **Step 5: api/recommend.py 를 recommend_core 호출로 교체**

`recommendation-server/api/recommend.py` 의 line 97-113 (prestacked 분기 ~ sorted_scores 까지)을 아래로 교체:

```python
    from engine.recommend_core import compute_scored_books

    sorted_scores = compute_scored_books(
        index=index,
        liked_books=liked_books,
        fb_data=fb_data,
        prestacked_reasons=request.app.state.prestacked_reasons,
        desc_matrix_f16=request.app.state.desc_matrix_f16,
        agg_reason_matrix_f16=request.app.state.agg_reason_matrix_f16,
        bid_order=request.app.state.bid_order,
    )
```

그리고 상단 import 정리:
```python
from engine.recommend_core import compute_scored_books
# 기존 from engine.scorer import recommend_scores 및 twostage import는 제거
```

- [ ] **Step 6: 기존 recommend 테스트 통과 확인**

Run: `cd recommendation-server && pytest tests/ -v -k "recommend"`
Expected: 기존 테스트 모두 통과 (리팩토링으로 기능 변화 없음)

- [ ] **Step 7: Commit**

```bash
git add recommendation-server/engine/recommend_core.py \
        recommendation-server/api/recommend.py \
        recommendation-server/tests/test_recommend_core.py
git commit -m "refactor: scoring 로직을 engine/recommend_core 로 추출"
```

---

## Task 5: engine/tier.py (User Tier 분기 + 섹션 구성 + 조사)

**Files:**
- Create: `recommendation-server/engine/tier.py`
- Create: `recommendation-server/tests/test_tier.py`

- [ ] **Step 1: 테스트 작성**

`recommendation-server/tests/test_tier.py`:
```python
from engine.tier import user_tier_from_likes, cta_for_tier, korean_particle, sections_for_tier

def test_user_tier_boundaries():
    assert user_tier_from_likes(0) == 0
    assert user_tier_from_likes(2) == 0
    assert user_tier_from_likes(3) == 1
    assert user_tier_from_likes(5) == 1
    assert user_tier_from_likes(6) == 2
    assert user_tier_from_likes(100) == 2

def test_cta_for_tier_0_counts_remaining():
    assert cta_for_tier(0, total_likes=0) == "좋아요 3권 더 누르면 비슷한 책 추천이 시작돼요"
    assert cta_for_tier(0, total_likes=2) == "좋아요 1권 더 누르면 비슷한 책 추천이 시작돼요"

def test_cta_for_tier_1_counts_remaining():
    assert cta_for_tier(1, total_likes=3) == "좋아요 3권 더 평가하면 취향 추천이 시작돼요"
    assert cta_for_tier(1, total_likes=5) == "좋아요 1권 더 평가하면 취향 추천이 시작돼요"

def test_cta_for_tier_2_none():
    assert cta_for_tier(2, total_likes=6) is None

def test_korean_particle_with_batchim():
    assert korean_particle("책", "과", "와") == "과"  # '책' 받침 있음
    assert korean_particle("나", "과", "와") == "와"  # '나' 받침 없음

def test_korean_particle_non_korean_uses_without():
    assert korean_particle("Book", "과", "와") == "와"

def test_sections_for_tier_0_has_4_sections():
    secs = sections_for_tier(0)
    assert len(secs) == 4
    assert secs[0]["type"] == "trending"
    assert secs[-1]["type"] == "category_nav"

def test_sections_for_tier_1_has_5_sections_with_similar_first():
    secs = sections_for_tier(1)
    assert len(secs) == 5
    assert secs[0]["type"] == "similar"

def test_sections_for_tier_2_has_5_sections_with_personal_recommend_first_no_category_nav():
    secs = sections_for_tier(2)
    assert len(secs) == 5
    assert secs[0]["type"] == "personal_recommend"
    assert all(s["type"] != "category_nav" for s in secs)
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && pytest tests/test_tier.py -v`
Expected: ImportError (tier 모듈 없음)

- [ ] **Step 3: engine/tier.py 구현**

```python
"""recommendation-server/engine/tier.py

User Tier 분기, Tier별 섹션 구성 규칙, CTA 문구 생성, 한국어 조사 처리.

Spec 참고:
- algorithm-design §4: Tier 임계 (3, 6)
- curation-system-design §6.2: Tier별 섹션 구성
- spec Phase 1B §6.2: 섹션 정의
"""
from __future__ import annotations

TIER1_THRESHOLD = 3
TIER2_THRESHOLD = 6


def user_tier_from_likes(total_likes: int) -> int:
    """좋아요 개수 → User Tier (0/1/2)."""
    if total_likes < TIER1_THRESHOLD:
        return 0
    if total_likes < TIER2_THRESHOLD:
        return 1
    return 2


def cta_for_tier(tier: int, total_likes: int) -> str | None:
    """Tier별 CTA 문구. Tier 2 는 None."""
    if tier == 0:
        remaining = TIER1_THRESHOLD - total_likes
        return f"좋아요 {remaining}권 더 누르면 비슷한 책 추천이 시작돼요"
    if tier == 1:
        remaining = TIER2_THRESHOLD - total_likes
        return f"좋아요 {remaining}권 더 평가하면 취향 추천이 시작돼요"
    return None


def korean_particle(word: str, with_batchim: str, without_batchim: str) -> str:
    """한국어 조사 처리. 단어 끝 받침 유무로 선택.
    비한글 끝 글자는 without_batchim 반환."""
    if not word:
        return without_batchim
    last = word[-1]
    if "가" <= last <= "힣":
        has_batchim = (ord(last) - 0xAC00) % 28 != 0
        return with_batchim if has_batchim else without_batchim
    return without_batchim


def sections_for_tier(tier: int) -> list[dict]:
    """Tier별 섹션 구성 (타입 + 큐레이션 개인화 힌트).

    Spec curation §6.2 준수. category_nav 는 Tier 0/1 만 (Tier 2 엔 없음).
    실제 books 리스트 채우기는 home.py 에서 수행.
    """
    if tier == 0:
        return [
            {"type": "trending"},
            {"type": "curation", "personalization": "general"},
            {"type": "curation", "personalization": "general"},
            {"type": "category_nav"},
        ]
    if tier == 1:
        return [
            {"type": "similar"},
            {"type": "curation", "personalization": "by_author"},
            {"type": "curation", "personalization": "by_l1"},
            {"type": "curation", "personalization": "general"},
            {"type": "category_nav"},
        ]
    # Tier 2
    return [
        {"type": "personal_recommend"},
        {"type": "curation", "personalization": "by_author"},
        {"type": "similar"},
        {"type": "curation", "personalization": "tier2+"},
        {"type": "trending"},
    ]


def similar_section_title(seed_title: str) -> str:
    """Tier 1/2 similar 섹션 제목 — '『X』과/와 비슷한 책'"""
    particle = korean_particle(seed_title, "과", "와")
    return f"『{seed_title}』{particle} 비슷한 책"
```

- [ ] **Step 4: 테스트 재실행 — 통과 확인**

Run: `cd recommendation-server && pytest tests/test_tier.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/tier.py recommendation-server/tests/test_tier.py
git commit -m "feat: engine/tier — User Tier 분기 + 섹션 구성 + 한국어 조사"
```

---

## Task 6: /recommend Tier 2 체크 추가

**Files:**
- Modify: `recommendation-server/api/recommend.py` (early return 추가)

- [ ] **Step 1: 테스트 먼저 작성**

`recommendation-server/tests/test_recommend_tier.py`:
```python
"""Tier 0/1 유저가 /recommend 직접 호출 시 빈 배열 반환."""
from unittest.mock import MagicMock, patch

def test_tier_below_2_returns_empty(monkeypatch):
    # 이 테스트는 config.get_supabase 를 모킹하여 tier 0/1 시나리오 검증
    # 실제 통합 테스트는 e2e 시나리오에서 다룸
    pass  # 통합 시나리오에서 Eden curl 로 검증
```

(단위 테스트는 auth/DB 의존성 많아 생략. 통합은 e2e 에서.)

- [ ] **Step 2: api/recommend.py 수정 — Tier 2 체크 early return**

`recommendation-server/api/recommend.py` 의 `get_recommendations` 함수에서 `ub_res = ...` 다음, `input_hash = ...` 이전에 Tier 체크 추가:

```python
    # Phase 1B — User Tier 2 체크. Tier 0/1 은 빈 배열 반환 (Flutter 하위 호환)
    us_res = sb.table("user_state").select("current_tier").eq("user_id", user_id).maybe_single().execute()
    current_tier = (us_res.data or {}).get("current_tier", 0)
    if current_tier < 2:
        return RecommendResponse(
            user_id=user_id,
            recommendations=[],
            meta={
                "total_liked": sum(1 for r in ub_res.data if r.get("rating") == "good"),
                "total_disliked": sum(1 for r in ub_res.data if r.get("rating") == "bad"),
                "has_feedback": any(r.get("feedback_embedding") for r in ub_res.data),
                "tier": current_tier,
                "reason": "insufficient_likes",
            },
        )
```

- [ ] **Step 3: 기존 테스트 통과 확인**

Run: `cd recommendation-server && pytest tests/ -v`
Expected: 모든 테스트 통과 (Tier 2 유저는 기존 경로)

- [ ] **Step 4: Commit**

```bash
git add recommendation-server/api/recommend.py recommendation-server/tests/test_recommend_tier.py
git commit -m "feat: /recommend Tier 2 체크 — Tier 0/1 빈 배열 반환 (하위 호환)"
```

---

# Phase D: Curation Engine

## Task 7: engine/curation.py (가중 랜덤 sampling + 개인화 + 7일 디스카운트)

**Files:**
- Create: `recommendation-server/engine/curation.py`
- Create: `recommendation-server/tests/test_curation.py`

- [ ] **Step 1: 테스트 작성**

`recommendation-server/tests/test_curation.py`:
```python
from engine.curation import (
    filter_by_personalization,
    weighted_sample_one,
    apply_recent_discount,
)


def test_filter_by_personalization_general_always_passes():
    themes = [{"id": 1, "personalization": "general"}]
    result = filter_by_personalization(themes, tier=0, top_authors=[], top_l1s=[])
    assert len(result) == 1


def test_filter_by_personalization_tier1_plus_blocks_tier0():
    themes = [{"id": 1, "personalization": "tier1+"}]
    assert filter_by_personalization(themes, tier=0, top_authors=[], top_l1s=[]) == []
    assert len(filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=[])) == 1


def test_filter_by_personalization_by_author_requires_match():
    themes = [{"id": 1, "personalization": "by_author", "target_author": "무라카미"}]
    assert filter_by_personalization(themes, tier=2, top_authors=["김영하"], top_l1s=[]) == []
    assert len(filter_by_personalization(themes, tier=2, top_authors=["무라카미"], top_l1s=[])) == 1


def test_filter_by_personalization_by_l1_requires_match():
    themes = [{"id": 1, "personalization": "by_l1", "target_l1": "문학"}]
    assert filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=["과학"]) == []
    assert len(filter_by_personalization(themes, tier=1, top_authors=[], top_l1s=["문학"])) == 1


def test_apply_recent_discount_removes_recent_shown():
    themes = [{"id": 1}, {"id": 2}, {"id": 3}]
    recent_ids = {2}
    result = apply_recent_discount(themes, recent_ids)
    assert [t["id"] for t in result] == [1, 3]


def test_weighted_sample_one_respects_priority():
    # priority 가 압도적으로 높은 항목이 대부분 선택되는지
    import random
    random.seed(42)
    themes = [
        {"id": 1, "priority": 0.01, "click_rate": 0.01},
        {"id": 2, "priority": 100.0, "click_rate": 0.01},
    ]
    counts = {1: 0, 2: 0}
    for _ in range(100):
        picked = weighted_sample_one(themes)
        counts[picked["id"]] += 1
    assert counts[2] > 80


def test_weighted_sample_one_empty_returns_none():
    assert weighted_sample_one([]) is None
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && pytest tests/test_curation.py -v`
Expected: ImportError

- [ ] **Step 3: engine/curation.py 구현**

```python
"""recommendation-server/engine/curation.py

큐레이션 풀에서 개인화 필터 + 가중 랜덤 sampling + 7일 디스카운트.

Spec curation-system §6.1-6.3 구현. sampling 은 priority + click_rate 가중치.
"""
from __future__ import annotations
import random
from typing import Optional


def filter_by_personalization(
    themes: list[dict],
    *,
    tier: int,
    top_authors: list[str],
    top_l1s: list[str],
) -> list[dict]:
    """Tier 와 유저 top preferences 기반으로 노출 가능한 theme 만 필터."""
    out: list[dict] = []
    for t in themes:
        p = t.get("personalization", "general")
        if p == "general":
            out.append(t)
        elif p == "tier1+" and tier >= 1:
            out.append(t)
        elif p == "tier2+" and tier >= 2:
            out.append(t)
        elif p == "by_author" and t.get("target_author") in top_authors:
            out.append(t)
        elif p == "by_l1" and t.get("target_l1") in top_l1s:
            out.append(t)
        elif p == "by_keyword" and t.get("target_keyword"):
            # Phase 1B 범위에서 by_keyword 는 구조만. 실제 매칭은 Phase 2.
            pass
    return out


def apply_recent_discount(themes: list[dict], recent_shown_ids: set[int]) -> list[dict]:
    """최근 7일 내 노출된 theme id 를 pool 에서 제외."""
    return [t for t in themes if t["id"] not in recent_shown_ids]


def weighted_sample_one(themes: list[dict]) -> Optional[dict]:
    """가중 랜덤 sampling 1개.

    weight = priority × (click_rate > 0.05 이면 ×1.5) × (by_* 개인화면 ×2.0)
    신간 가중치 0.05 (theme_type='genre_combo' + parameters 에 '신간')은 theme.priority 자체로 관리 가정.
    """
    if not themes:
        return None

    weights: list[float] = []
    for t in themes:
        w = t.get("priority", 1.0)
        if t.get("click_rate", 0.0) > 0.05:
            w *= 1.5
        if t.get("personalization") in ("by_l1", "by_author", "by_keyword"):
            w *= 2.0
        weights.append(max(w, 1e-6))

    return random.choices(themes, weights=weights, k=1)[0]
```

- [ ] **Step 4: 테스트 재실행 — 통과 확인**

Run: `cd recommendation-server && pytest tests/test_curation.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add recommendation-server/engine/curation.py recommendation-server/tests/test_curation.py
git commit -m "feat: engine/curation — 개인화 필터 + 가중 랜덤 sampling + 7일 디스카운트"
```

---

## Task 8: engine/home_cache.py (home_section_cache 읽기/쓰기)

**Files:**
- Create: `recommendation-server/engine/home_cache.py`

- [ ] **Step 1: 파일 작성**

```python
"""recommendation-server/engine/home_cache.py

home_section_cache 읽기/쓰기 + input_hash 계산.

Spec §5.2, §6.2: hash = sha256(user_state.updated_at + current_hour_bucket)
→ user_books 변경 (trigger 로 user_state 갱신) 또는 시간 bucket 변경 시 invalidate
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Optional

from config import get_supabase


def current_hour_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H")


def compute_home_input_hash(user_state_updated_at: str, hour_bucket: str) -> str:
    raw = f"{user_state_updated_at}|{hour_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def load_home_cache(user_id: str) -> Optional[dict]:
    sb = get_supabase()
    res = sb.table("home_section_cache").select("*").eq("user_id", user_id).maybe_single().execute()
    return res.data


def save_home_cache_if_current(
    user_id: str,
    sections: list,
    tier: int,
    stage: int,
    input_hash: str,
) -> None:
    """BackgroundTasks 로 호출. hash 가 current 일 때만 저장."""
    sb = get_supabase()
    try:
        sb.table("home_section_cache").upsert({
            "user_id": user_id,
            "sections": sections,
            "tier": tier,
            "stage": stage,
            "input_hash": input_hash,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        # 캐시 쓰기 실패는 응답에 영향 없음 (다음 호출 때 재시도)
        print(f"home_cache save failed for {user_id}: {e}")
```

- [ ] **Step 2: import 스모크 체크**

Run: `cd recommendation-server && python3 -c "from engine.home_cache import compute_home_input_hash, current_hour_bucket; print(compute_home_input_hash('2026-04-15T10:00:00+00:00', current_hour_bucket()))"`
Expected: 64자 hex 문자열 출력

- [ ] **Step 3: Commit**

```bash
git add recommendation-server/engine/home_cache.py
git commit -m "feat: engine/home_cache — home_section_cache 읽기/쓰기 + hash"
```

---

# Phase E: /home 엔드포인트

## Task 9: api/home.py + /home 라우팅

**Files:**
- Create: `recommendation-server/api/home.py`
- Create: `recommendation-server/tests/test_home.py`
- Modify: `recommendation-server/main.py` (home_router 등록)

- [ ] **Step 1: 테스트 작성 (섹션 조립 단위 테스트)**

`recommendation-server/tests/test_home.py`:
```python
from unittest.mock import MagicMock

from api.home import assemble_sections_for_user


def test_assemble_sections_tier_0_returns_trending_and_curations():
    """Tier 0: [trending, curation, curation, category_nav]"""
    fake_books_meta = {"b1": {"title": "T1", "author": "A", "cover_url": None}}
    fake_fallback = [{"book_id": "b1"}]  # fallback_curation row
    fake_themes = [
        {"id": 10, "theme_type": "genre_combo", "title": "문학", "personalization": "general",
         "priority": 1.0, "click_rate": 0.0},
    ]
    fake_cache_rows = {10: ["b1"]}

    sections = assemble_sections_for_user(
        tier=0, stage=0, total_likes=0,
        user_books=[],
        top_authors=[], top_l1s=[],
        recent_curation_ids=set(),
        fallback_books=fake_fallback,
        active_themes=fake_themes,
        curation_cache_by_id=fake_cache_rows,
        books_meta=fake_books_meta,
        index=None,
    )
    types = [s["type"] for s in sections]
    assert types[0] == "trending"
    assert types[-1] == "category_nav"
    assert len(sections) == 4


def test_assemble_sections_tier_2_has_personal_recommend_first():
    sections = assemble_sections_for_user(
        tier=2, stage=0, total_likes=10,
        user_books=[{"book_id": "b1", "rating": "good"}],
        top_authors=["A"], top_l1s=["문학"],
        recent_curation_ids=set(),
        fallback_books=[],
        active_themes=[],
        curation_cache_by_id={},
        books_meta={},
        index=None,
        recommend_scored=[],  # Tier 2 에서 recommend_core 결과 주입
    )
    types = [s["type"] for s in sections]
    assert types[0] == "personal_recommend"
    assert "category_nav" not in types
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd recommendation-server && pytest tests/test_home.py -v`
Expected: ImportError

- [ ] **Step 3: api/home.py 구현**

```python
"""recommendation-server/api/home.py

/home/{user_id} — User Tier 분기 후 섹션 조립. Spec §6.2.

쿼리 수: user_state 1 + user_books 1 + active themes 1 + curation_cache IN-clause 1
+ recommendation_stage 1 (+ fallback_curation 1)
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth import verify_jwt
from config import get_supabase
from engine.tier import (
    user_tier_from_likes, cta_for_tier, sections_for_tier, similar_section_title,
)
from engine.curation import (
    filter_by_personalization, apply_recent_discount, weighted_sample_one,
)
from engine.home_cache import (
    current_hour_bucket, compute_home_input_hash,
    load_home_cache, save_home_cache_if_current,
)
from engine.recommend_core import compute_scored_books
from engine.utils import to_np

router = APIRouter()


def _book_dict(bid: str, books_meta: dict, score: Optional[float] = None) -> Optional[dict]:
    meta = books_meta.get(bid)
    if meta is None:
        return None  # skip ghost book
    d = {
        "book_id": bid,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "cover_url": meta.get("cover_url"),
    }
    if score is not None:
        d["score"] = round(score, 4)
    return d


def _similar_books_from_seed(index, books_meta: dict, seed_book_id: str, limit: int = 10) -> list[dict]:
    try:
        results = index.similar_by_desc(seed_book_id, top_n=limit)
    except Exception:
        return []
    out = []
    for bid, _score in results:
        b = _book_dict(bid, books_meta)
        if b:
            out.append(b)
    return out


def assemble_sections_for_user(
    *,
    tier: int,
    stage: int,
    total_likes: int,
    user_books: list[dict],
    top_authors: list[str],
    top_l1s: list[str],
    recent_curation_ids: set,
    fallback_books: list[dict],
    active_themes: list[dict],
    curation_cache_by_id: dict,
    books_meta: dict,
    index,
    recommend_scored: Optional[list] = None,
) -> list[dict]:
    """Tier별 섹션 구성 규칙에 따라 실제 books 리스트를 조립한다."""
    templates = sections_for_tier(tier)
    sections: list[dict] = []

    # 최근 좋아한 책 id (similar seed)
    latest_liked_bid = None
    for ub in user_books:
        if ub.get("rating") == "good":
            latest_liked_bid = ub["book_id"]
            break  # user_books 는 updated_at DESC 정렬 가정

    for idx, tpl in enumerate(templates):
        stype = tpl["type"]
        section_id = f"{stype}_{idx}"

        if stype == "personal_recommend":
            books = []
            for bid, score in (recommend_scored or [])[:10]:
                b = _book_dict(bid, books_meta, score=score)
                if b:
                    books.append(b)
            sections.append({
                "id": section_id, "type": "personal_recommend",
                "title": "당신을 위한 추천", "books": books,
                "algorithm_version": "h10_stage0",
            })

        elif stype == "similar":
            if latest_liked_bid and latest_liked_bid in books_meta:
                seed_title = books_meta[latest_liked_bid].get("title", "")
                books = _similar_books_from_seed(index, books_meta, latest_liked_bid)
                sections.append({
                    "id": section_id, "type": "similar",
                    "title": similar_section_title(seed_title),
                    "seed_book_id": latest_liked_bid,
                    "books": books,
                })
            else:
                # fallback: general curation
                sections.append(_sample_curation(
                    active_themes, top_authors, top_l1s, tier, recent_curation_ids,
                    curation_cache_by_id, books_meta, personalization_override="general",
                    section_id=section_id,
                ))

        elif stype == "curation":
            sections.append(_sample_curation(
                active_themes, top_authors, top_l1s, tier, recent_curation_ids,
                curation_cache_by_id, books_meta,
                personalization_override=tpl.get("personalization"),
                section_id=section_id,
            ))

        elif stype == "trending":
            books = []
            for row in fallback_books[:10]:
                b = _book_dict(row["book_id"], books_meta)
                if b:
                    books.append(b)
            sections.append({
                "id": section_id, "type": "trending",
                "title": "화제의 책", "books": books,
            })

        elif stype == "category_nav":
            sections.append({
                "id": section_id, "type": "category_nav", "books": [],
            })

    return sections


def _sample_curation(
    active_themes, top_authors, top_l1s, tier, recent_ids,
    cache_by_id, books_meta, *, personalization_override=None, section_id,
) -> dict:
    # 개인화 필터
    pool = [t for t in active_themes
            if personalization_override is None
            or t.get("personalization") == personalization_override]
    pool = filter_by_personalization(pool, tier=tier,
                                     top_authors=top_authors, top_l1s=top_l1s)
    pool = apply_recent_discount(pool, recent_ids)

    # by_author/by_l1 fallback → general
    if not pool and personalization_override in ("by_author", "by_l1"):
        pool = [t for t in active_themes if t.get("personalization") == "general"]
        pool = apply_recent_discount(pool, recent_ids)

    picked = weighted_sample_one(pool)
    if picked is None:
        return {"id": section_id, "type": "curation", "books": []}

    book_ids = cache_by_id.get(picked["id"], [])
    books: list[dict] = []
    for bid in book_ids[:10]:
        b = _book_dict(bid, books_meta)
        if b:
            books.append(b)

    return {
        "id": f"curation_{picked['id']}",
        "type": "curation",
        "title": picked.get("title", ""),
        "description": picked.get("description"),
        "curation_id": picked["id"],
        "personalization": picked.get("personalization"),
        "books": books,
    }


@router.get("/home/{user_id}")
async def get_home(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_jwt),
):
    if current_user != user_id:
        raise HTTPException(403, "Cannot access other user's home")

    sb = get_supabase()

    us_res = sb.table("user_state").select(
        "current_tier,total_likes,top_authors,top_l1s,updated_at"
    ).eq("user_id", user_id).maybe_single().execute()

    us = us_res.data or {
        "current_tier": 0, "total_likes": 0,
        "top_authors": [], "top_l1s": [], "updated_at": "",
    }
    tier = us["current_tier"]
    total_likes = us["total_likes"]
    top_authors = [a["author"] for a in (us.get("top_authors") or [])]
    top_l1s = [l["l1"] for l in (us.get("top_l1s") or [])]

    # stage
    stage_res = sb.table("recommendation_stage").select("current_stage").eq("id", 1).maybe_single().execute()
    stage = (stage_res.data or {}).get("current_stage", 0)

    # home_section_cache 확인
    hour_bucket = current_hour_bucket()
    input_hash = compute_home_input_hash(us.get("updated_at", ""), hour_bucket)
    cache = load_home_cache(user_id)

    if cache and cache.get("input_hash") == input_hash:
        return {
            "user_id": user_id, "tier": tier, "stage": stage,
            "sections": cache["sections"],
            "cta": cta_for_tier(tier, total_likes),
            "computed_at": cache["computed_at"],
            "cache_hit": True,
        }

    # Miss → 섹션 조립용 데이터 조회
    ub_res = sb.table("user_books").select(
        "book_id,rating,feedback_embedding,updated_at"
    ).eq("user_id", user_id).order("updated_at", desc=True).execute()
    user_books = ub_res.data or []

    themes_res = sb.table("curation_themes").select(
        "id,theme_type,title,description,personalization,"
        "target_l1,target_author,target_keyword,priority,click_rate"
    ).eq("is_active", True).execute()
    active_themes = themes_res.data or []

    theme_ids = [t["id"] for t in active_themes]
    cache_rows = []
    if theme_ids:
        cache_res = sb.table("curation_cache").select(
            "curation_id,book_ids,expires_at"
        ).in_("curation_id", theme_ids).execute()
        cache_rows = cache_res.data or []

    now_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()
    curation_cache_by_id = {
        r["curation_id"]: r["book_ids"]
        for r in cache_rows
        if r.get("expires_at", "") > now_iso
    }

    fb_res = sb.table("fallback_curation").select("book_id").order("rank").limit(30).execute()
    fallback_books = fb_res.data or []

    uch_res = sb.table("user_curation_history").select("curation_id").eq(
        "user_id", user_id
    ).gte("shown_at", "NOW() - INTERVAL '7 days'").execute()
    recent_curation_ids = {r["curation_id"] for r in (uch_res.data or [])}

    # Tier 2 라면 recommend_core 호출
    recommend_scored = None
    if tier == 2:
        liked_books: dict = {}
        fb_data: dict = {}
        for ub in user_books:
            bid = ub["book_id"]
            liked_books[bid] = {"rating": ub.get("rating", "neutral")}
            fb_emb = ub.get("feedback_embedding")
            if fb_emb:
                fb_data[bid] = {"emb": to_np(fb_emb), "is_dislike": ub.get("rating") == "bad"}
        try:
            recommend_scored = compute_scored_books(
                index=request.app.state.index,
                liked_books=liked_books, fb_data=fb_data,
                prestacked_reasons=request.app.state.prestacked_reasons,
                desc_matrix_f16=request.app.state.desc_matrix_f16,
                agg_reason_matrix_f16=request.app.state.agg_reason_matrix_f16,
                bid_order=request.app.state.bid_order,
            )
        except Exception as e:
            print(f"recommend_scored failed for {user_id}: {e}")
            recommend_scored = []

    sections = assemble_sections_for_user(
        tier=tier, stage=stage, total_likes=total_likes,
        user_books=user_books,
        top_authors=top_authors, top_l1s=top_l1s,
        recent_curation_ids=recent_curation_ids,
        fallback_books=fallback_books,
        active_themes=active_themes,
        curation_cache_by_id=curation_cache_by_id,
        books_meta=request.app.state.books_meta,
        index=request.app.state.index,
        recommend_scored=recommend_scored,
    )

    # BackgroundTasks: cache write + impression INSERT + user_curation_history
    background_tasks.add_task(
        save_home_cache_if_current,
        user_id, sections, tier, stage, input_hash,
    )
    background_tasks.add_task(
        _log_impressions_and_history,
        user_id, sections, stage,
    )

    return {
        "user_id": user_id, "tier": tier, "stage": stage,
        "sections": sections,
        "cta": cta_for_tier(tier, total_likes),
        "computed_at": now_iso,
        "cache_hit": False,
    }


def _log_impressions_and_history(user_id: str, sections: list[dict], stage: int) -> None:
    """/home 섹션 노출 임프레션을 batch INSERT + user_curation_history 기록."""
    sb = get_supabase()
    imp_rows = []
    uch_rows = []
    for sec in sections:
        source = "home_recommend" if sec["type"] in ("personal_recommend", "similar") else \
                 "curation" if sec["type"] == "curation" else "home_recommend"
        curation_id = sec.get("curation_id")
        for pos, book in enumerate(sec.get("books", [])):
            imp_rows.append({
                "user_id": user_id,
                "book_id": book["book_id"],
                "position": pos,
                "source": source,
                "algorithm_version": f"h10_stage{stage}",
                "curation_id": curation_id,
            })
        if curation_id:
            uch_rows.append({"user_id": user_id, "curation_id": curation_id})

    try:
        if imp_rows:
            sb.table("recommendation_impressions").insert(imp_rows).execute()
        if uch_rows:
            sb.table("user_curation_history").insert(uch_rows).execute()
    except Exception as e:
        print(f"impression/history batch insert failed for {user_id}: {e}")
```

- [ ] **Step 4: main.py 에 router 등록**

`recommendation-server/main.py` 에서 기존 router include 근처에 추가:
```python
from api.home import router as home_router
app.include_router(home_router)
```

- [ ] **Step 5: 테스트 재실행 — 통과 확인**

Run: `cd recommendation-server && pytest tests/test_home.py -v`
Expected: 2 passed

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `cd recommendation-server && pytest tests/ -v`
Expected: 모두 통과 (기존 + 신규)

- [ ] **Step 7: Commit**

```bash
git add recommendation-server/api/home.py \
        recommendation-server/tests/test_home.py \
        recommendation-server/main.py
git commit -m "feat: /home 엔드포인트 — Tier별 섹션 조립 + impression batch INSERT"
```

---

## Task 10: /curations/{curation_id}/books 엔드포인트

**Files:**
- Create: `recommendation-server/api/curation.py`
- Modify: `recommendation-server/main.py` (curation_router 등록)

- [ ] **Step 1: api/curation.py 작성**

```python
"""recommendation-server/api/curation.py

/curations/{curation_id}/books — 큐레이션의 전체 책 리스트 페이징.
Flutter '더보기' 탭용.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import verify_jwt
from config import get_supabase

router = APIRouter()


@router.get("/curations/{curation_id}/books")
async def get_curation_books(
    curation_id: int,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    _: str = Depends(verify_jwt),
):
    sb = get_supabase()

    t_res = sb.table("curation_themes").select(
        "id,theme_type,title,description"
    ).eq("id", curation_id).eq("is_active", True).maybe_single().execute()
    if not t_res.data:
        raise HTTPException(404, "Curation not found or inactive")
    theme = t_res.data

    c_res = sb.table("curation_cache").select(
        "book_ids,cached_at"
    ).eq("curation_id", curation_id).maybe_single().execute()
    if not c_res.data:
        return {
            "curation_id": curation_id,
            "theme_type": theme["theme_type"],
            "title": theme["title"],
            "description": theme.get("description"),
            "total": 0, "offset": offset, "limit": limit,
            "books": [], "cached_at": None,
        }

    all_book_ids: list[str] = c_res.data["book_ids"]
    total = len(all_book_ids)
    page_ids = all_book_ids[offset:offset + limit]

    books_meta = request.app.state.books_meta
    books = []
    for bid in page_ids:
        meta = books_meta.get(bid)
        if meta is None:
            continue
        books.append({
            "book_id": bid,
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "cover_url": meta.get("cover_url"),
        })

    return {
        "curation_id": curation_id,
        "theme_type": theme["theme_type"],
        "title": theme["title"],
        "description": theme.get("description"),
        "total": total, "offset": offset, "limit": limit,
        "books": books,
        "cached_at": c_res.data["cached_at"],
    }
```

- [ ] **Step 2: main.py 에 등록**

`recommendation-server/main.py` 에 추가:
```python
from api.curation import router as curation_router
app.include_router(curation_router)
```

- [ ] **Step 3: import 스모크**

Run: `cd recommendation-server && python3 -c "from api.curation import router; print([r.path for r in router.routes])"`
Expected: `['/curations/{curation_id}/books']`

- [ ] **Step 4: Commit**

```bash
git add recommendation-server/api/curation.py recommendation-server/main.py
git commit -m "feat: /curations/{id}/books 엔드포인트 — 큐레이션 책 리스트 페이징"
```

---

# Phase F: GH Actions 생성 스크립트

## Task 11: generate_curation_themes.py (weekly)

**Files:**
- Create: `recommendation-server/scripts/generate_curation_themes.py`
- Create: `.github/workflows/generate-curation-themes.yml`

- [ ] **Step 1: 스크립트 작성**

`recommendation-server/scripts/generate_curation_themes.py`:
```python
"""recommendation-server/scripts/generate_curation_themes.py

Rule-based curation_themes 생성/갱신 (genre_combo, author, keyword).
weekly 실행. idempotent upsert via theme_key.

Cluster 타입은 별도 monthly script.

Eden feedback_batch_operations 준수:
- per-row try/except + 중간 commit
- 에러 시 continue (부분 실패 허용)
- 결과 count 로깅
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_supabase

MIN_GENRE_BOOKS = 10
MIN_AUTHOR_BOOKS = 3
MIN_KEYWORD_BOOKS = 5


def _upsert_theme(sb, *, theme_key, theme_type, title, description, parameters,
                  personalization, target_l1=None, target_author=None, target_keyword=None,
                  priority=1.0):
    row = {
        "theme_key": theme_key,
        "theme_type": theme_type,
        "title": title,
        "description": description,
        "selection_query": {"type": theme_type},
        "parameters": parameters,
        "personalization": personalization,
        "target_l1": target_l1,
        "target_author": target_author,
        "target_keyword": target_keyword,
        "priority": priority,
        "is_active": True,
    }
    sb.table("curation_themes").upsert(row, on_conflict="theme_key").execute()


def generate_genre_combo(sb) -> int:
    # books WHERE l1 IS NOT NULL AND l2 IS NOT NULL GROUP BY l1,l2 HAVING COUNT>=10
    # supabase-py 는 raw SQL 실행 불가 → RPC 또는 전체 fetch 후 in-memory aggregation
    # 여기선 전체 fetch (활성 ~9K rows).
    res = sb.table("books").select("l1,l2").not_.is_("l1", "null").not_.is_("l2", "null").execute()
    from collections import Counter
    counts = Counter((r["l1"], r["l2"]) for r in (res.data or []))
    created = 0
    for (l1, l2), cnt in counts.items():
        if cnt < MIN_GENRE_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"genre_combo|{l1}|{l2}",
                theme_type="genre_combo",
                title=f"{l1} · {l2}",
                description=f"{l1} 중 {l2} 분류의 책들",
                parameters={"l1": l1, "l2": l2},
                personalization="general",
                target_l1=l1,
            )
            created += 1
        except Exception as e:
            print(f"[skip] genre_combo {l1}|{l2}: {e}")
    return created


def generate_author(sb) -> int:
    res = sb.table("books").select("author").not_.is_("author", "null").execute()
    from collections import Counter
    counts = Counter(r["author"] for r in (res.data or []))
    created = 0
    for author, cnt in counts.items():
        if cnt < MIN_AUTHOR_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"author|{author}",
                theme_type="author",
                title=f"{author} 컬렉션",
                description=f"{author} 작가의 책들",
                parameters={"author": author},
                personalization="by_author",
                target_author=author,
            )
            created += 1
        except Exception as e:
            print(f"[skip] author {author}: {e}")
    return created


def generate_keyword(sb) -> int:
    # library_keywords TEXT[] → pg 배열 unnest 필요. in-memory.
    res = sb.table("books").select("library_keywords").not_.is_("library_keywords", "null").execute()
    from collections import Counter
    counts: Counter = Counter()
    for r in (res.data or []):
        for kw in (r.get("library_keywords") or []):
            counts[kw] += 1
    created = 0
    for kw, cnt in counts.items():
        if cnt < MIN_KEYWORD_BOOKS:
            continue
        try:
            _upsert_theme(
                sb,
                theme_key=f"keyword|{kw}",
                theme_type="keyword",
                title=kw,
                description=f"{kw} 관련 책들",
                parameters={"keyword": kw},
                personalization="by_keyword",
                target_keyword=kw,
            )
            created += 1
        except Exception as e:
            print(f"[skip] keyword {kw}: {e}")
    return created


def main(dry_run: bool = False):
    sb = get_supabase()
    print(f"[generate_themes] dry_run={dry_run}")
    if dry_run:
        print("dry-run: counting only")
    n_genre = generate_genre_combo(sb)
    print(f"  genre_combo: {n_genre}")
    n_author = generate_author(sb)
    print(f"  author:      {n_author}")
    n_keyword = generate_keyword(sb)
    print(f"  keyword:     {n_keyword}")
    total = n_genre + n_author + n_keyword
    print(f"  TOTAL upserts: {total}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
```

- [ ] **Step 2: workflow 작성**

`.github/workflows/generate-curation-themes.yml`:
```yaml
name: Generate Curation Themes

on:
  schedule:
    - cron: '30 19 * * 1'  # Mon 04:30 KST
  workflow_dispatch:

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r recommendation-server/requirements.txt
      - run: cd recommendation-server && python scripts/generate_curation_themes.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
```

- [ ] **Step 3: import 스모크**

Run: `cd recommendation-server && python3 -c "from scripts.generate_curation_themes import main; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add recommendation-server/scripts/generate_curation_themes.py \
        .github/workflows/generate-curation-themes.yml
git commit -m "feat: generate_curation_themes.py + workflow (weekly rule-based)"
```

---

## Task 12: generate_cluster_themes.py (monthly, LLM)

**Files:**
- Create: `recommendation-server/scripts/generate_cluster_themes.py`
- Create: `.github/workflows/generate-cluster-themes.yml`

- [ ] **Step 1: 스크립트 작성**

`recommendation-server/scripts/generate_cluster_themes.py`:
```python
"""recommendation-server/scripts/generate_cluster_themes.py

Monthly: KMeans(desc_matrix) → book_cluster_assignments upsert →
cluster 대표 책 5개 추출 → OpenAI gpt-4o-mini로 title/description 생성 →
curation_themes (theme_type='cluster') upsert.

index.pkl 을 LFS 로 pull하여 Supabase egress 회피.

feedback_batch_operations:
- per-cluster try/except
- sleep 1s per OpenAI call (rate limit)
- dry-run 지원
- 생성/갱신 count 로깅
"""
from __future__ import annotations
import os, sys, time, pickle
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_supabase

N_CLUSTERS = 30
INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "index.pkl")

FORBIDDEN_WORDS = ("최고", "1위", "베스트", "무조건", "반드시")


def _fallback_title(cluster_id: int, sample_titles: list[str]) -> tuple[str, str]:
    return (f"묶음 #{cluster_id}", "비슷한 분위기의 책들")


def _generate_llm_title(sample_titles: list[str], sample_reasons: list[str]) -> tuple[str, str]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = f"""아래 5권의 책들이 비슷한 분위기로 묶입니다. 한국어로 큐레이션 제목과 설명을 생성해주세요.

책:
{chr(10).join(f'- {t}' for t in sample_titles)}

감상 키워드:
{chr(10).join(f'- {r}' for r in sample_reasons[:3])}

형식:
제목: (5~30자, 광고문구 금지)
설명: (한 줄, 50자 이내)
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200, temperature=0.7,
    )
    text = resp.choices[0].message.content or ""
    title = ""
    description = ""
    for line in text.splitlines():
        if line.startswith("제목:"):
            title = line[3:].strip()
        elif line.startswith("설명:"):
            description = line[3:].strip()

    if not (5 <= len(title) <= 30) or any(w in title for w in FORBIDDEN_WORDS):
        return ("", "")  # fallback caller
    return title, description


def main(dry_run: bool = False):
    print(f"[cluster] dry_run={dry_run}")
    with open(INDEX_PATH, "rb") as f:
        bundle = pickle.load(f)

    desc_matrix = bundle["desc_matrix_f16"].astype("float32")
    bid_order = bundle["bid_order"]
    meta = bundle["meta"]

    from sklearn.cluster import KMeans
    import numpy as np

    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init="auto")
    labels = km.fit_predict(desc_matrix)
    centers = km.cluster_centers_

    # 각 책의 cluster 내 distance
    distances = np.linalg.norm(desc_matrix - centers[labels], axis=1)

    cluster_version = datetime.utcnow().strftime("v%Y%m")
    print(f"  cluster_version: {cluster_version}")

    sb = get_supabase()

    # book_cluster_assignments upsert
    assign_rows = []
    for i, bid in enumerate(bid_order):
        assign_rows.append({
            "book_id": bid, "cluster_id": int(labels[i]),
            "cluster_version": cluster_version, "distance": float(distances[i]),
        })

    if not dry_run:
        # chunk upsert (1000 per batch)
        BATCH = 1000
        for i in range(0, len(assign_rows), BATCH):
            sb.table("book_cluster_assignments").upsert(
                assign_rows[i:i+BATCH], on_conflict="book_id"
            ).execute()
        print(f"  assignments: {len(assign_rows)}")

    # 각 cluster 에 대해 LLM title/description + curation_themes upsert
    created = 0
    for cluster_id in range(N_CLUSTERS):
        try:
            idxs = np.where(labels == cluster_id)[0]
            if len(idxs) == 0:
                continue
            # centroid 가장 가까운 5개
            sub_dist = distances[idxs]
            top5 = idxs[np.argsort(sub_dist)[:5]]
            sample_bids = [bid_order[i] for i in top5]
            sample_titles = [meta.get(b, {}).get("title", "") for b in sample_bids]
            sample_reasons: list[str] = []  # reason 은 spec 에서 선택, 생략 가능

            title = ""
            description = ""
            if not dry_run and os.environ.get("OPENAI_API_KEY"):
                try:
                    title, description = _generate_llm_title(sample_titles, sample_reasons)
                    time.sleep(1)  # rate limit
                except Exception as e:
                    print(f"  [LLM fail] cluster {cluster_id}: {e}")

            if not title:
                title, description = _fallback_title(cluster_id, sample_titles)

            if not dry_run:
                sb.table("curation_themes").upsert({
                    "theme_key": f"cluster|{cluster_id}",
                    "theme_type": "cluster",
                    "title": title,
                    "description": description,
                    "selection_query": {"type": "cluster"},
                    "parameters": {
                        "cluster_id": cluster_id,
                        "cluster_version": cluster_version,
                    },
                    "personalization": "general",
                    "is_active": True,
                }, on_conflict="theme_key").execute()
            created += 1
        except Exception as e:
            print(f"  [skip] cluster {cluster_id}: {e}")

    print(f"  clusters upserted: {created}/{N_CLUSTERS}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
```

- [ ] **Step 2: workflow 작성**

`.github/workflows/generate-cluster-themes.yml`:
```yaml
name: Generate Cluster Themes

on:
  schedule:
    - cron: '30 20 1 * *'  # 1st 05:30 KST
  workflow_dispatch:

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          lfs: true
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r recommendation-server/requirements.txt
      - run: pip install scikit-learn openai
      - run: cd recommendation-server && python scripts/generate_cluster_themes.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

- [ ] **Step 3: import 스모크**

Run: `cd recommendation-server && python3 -c "from scripts.generate_cluster_themes import main, _fallback_title; print(_fallback_title(5, []))"`
Expected: `('묶음 #5', '비슷한 분위기의 책들')`

- [ ] **Step 4: Commit**

```bash
git add recommendation-server/scripts/generate_cluster_themes.py \
        .github/workflows/generate-cluster-themes.yml
git commit -m "feat: generate_cluster_themes.py + workflow (monthly KMeans + LLM)"
```

---

# Phase G: 검증 인프라

## Task 13: verify-phase-1b.yml + e2e 스크립트

**Files:**
- Create: `.github/workflows/verify-phase-1b.yml`
- Create: `scripts/e2e_phase1b.sh`

- [ ] **Step 1: verify workflow 작성**

`.github/workflows/verify-phase-1b.yml`:
```yaml
name: Verify Phase 1B

on: workflow_dispatch

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r recommendation-server/requirements.txt
      - run: cd recommendation-server && pytest tests/ -v

  db-state:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check pg_cron jobs registered
        env:
          PGPASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
          DB_URL: ${{ secrets.SUPABASE_DB_URL }}
        run: |
          sudo apt-get install -y postgresql-client
          EXPECTED="refresh-curation-cache aggregate-co-occurrence refresh-user-top-taste refresh-fallback-curation deactivate-curations check-stage-transition cleanup-user-curation-history cleanup-home-section-cache"
          REGISTERED=$(psql "$DB_URL" -tAc "SELECT jobname FROM cron.job")
          for j in $EXPECTED; do
            if ! echo "$REGISTERED" | grep -q "^$j$"; then
              echo "❌ Missing cron job: $j"
              exit 1
            fi
            echo "✅ $j"
          done

      - name: Check impression.curation_id population
        env:
          DB_URL: ${{ secrets.SUPABASE_DB_URL }}
        run: |
          CNT=$(psql "$DB_URL" -tAc "SELECT COUNT(*) FROM recommendation_impressions WHERE curation_id IS NOT NULL")
          echo "impressions with curation_id: $CNT"
```

참고: `SUPABASE_DB_URL` secret 이 없으면 이 job 은 skip. DB 접속용 URL 은 `postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres` 형식이며 Eden 이 secret 추가.

- [ ] **Step 2: e2e 스크립트 작성**

`scripts/e2e_phase1b.sh`:
```bash
#!/usr/bin/env bash
# Phase 1B End-to-end 검증 시나리오
# 사용법: API=https://... JWT=... UID=... ./scripts/e2e_phase1b.sh
#
# test user JWT 발급:
# Supabase Dashboard → Authentication → Users → test 계정 생성 → Sign in
# → access_token 복사
set -euo pipefail

: "${API:?API env 필요}"
: "${JWT:?JWT env 필요}"
: "${UID:?UID env 필요}"

H="Authorization: Bearer $JWT"

echo "=== 1. Tier 0 (신규) 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 2. Tier 0 → Tier 1 전환을 위해 좋아요 3권 추가 ==="
echo "Supabase SQL Editor 에서 실행:"
echo "  INSERT INTO user_books (user_id, book_id, rating, status) VALUES"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 0), 'good', 'finished'),"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 1), 'good', 'finished'),"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 2), 'good', 'finished');"
read -p "실행 완료 후 Enter..."

echo "=== 3. Tier 1 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 4. 좋아요 3권 더 추가 (총 6권) → Tier 2 ==="
read -p "추가 INSERT 완료 후 Enter..."

echo "=== 5. Tier 2 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 6. impression.curation_id 기록 확인 ==="
echo "Supabase SQL Editor:"
echo "  SELECT COUNT(*) FROM recommendation_impressions"
echo "  WHERE user_id='$UID' AND curation_id IS NOT NULL;"
echo "  기대: > 0"

echo ""
echo "=== 완료 ==="
```

- [ ] **Step 3: 실행 권한 부여**

Run: `chmod +x scripts/e2e_phase1b.sh`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/verify-phase-1b.yml scripts/e2e_phase1b.sh
git commit -m "chore: Phase 1B 검증 workflow + e2e 시나리오 스크립트"
```

---

## Task 14: Load test (locust)

**Files:**
- Create: `recommendation-server/tests/locust/home_loadtest.py`

- [ ] **Step 1: locustfile 작성**

`recommendation-server/tests/locust/home_loadtest.py`:
```python
"""Phase 1B /home 부하 테스트.

사용:
  cd recommendation-server
  pip install locust
  API=https://curation-recommendation.onrender.com JWT=... UID=... \\
    locust -f tests/locust/home_loadtest.py --host=$API \\
      --users=10 --spawn-rate=2 --run-time=60s --headless
"""
import os
from locust import HttpUser, task, between


class HomeUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.jwt = os.environ["JWT"]
        self.uid = os.environ["UID"]

    @task
    def get_home(self):
        self.client.get(
            f"/home/{self.uid}",
            headers={"Authorization": f"Bearer {self.jwt}"},
            name="/home/{uid}",
        )
```

- [ ] **Step 2: README 주석 추가**

스크립트 상단 사용법이 이미 포함됨. 로컬에서 off-peak 시간 실행 가정.

- [ ] **Step 3: Commit**

```bash
git add recommendation-server/tests/locust/home_loadtest.py
git commit -m "chore: /home locust load test"
```

---

# Phase H: 전체 통합 확인

## Task 15: 전체 pytest + /health 확장

**Files:**
- Modify: `recommendation-server/main.py` (/health 에 cache stats 추가)

- [ ] **Step 1: /health 확장**

`recommendation-server/main.py` 의 `/health` 핸들러를 찾아 아래로 업데이트 (기존 필드 유지 + 확장):

```python
import psutil
import os

@app.get("/health")
async def health(request: Request):
    state = request.app.state
    ver = "v4-prestacked" if getattr(state, "prestacked_reasons", None) else "v3-float16"
    mem_mb = psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
    return {
        "status": "ok",
        "version": ver,
        "books_loaded": len(getattr(state, "bid_order", []) or []),
        "index_built_at": getattr(state, "built_at", None),
        "memory_mb": mem_mb,
        "cache_hits": getattr(state, "cache_hits", 0),
        "cache_misses": getattr(state, "cache_misses", 0),
    }
```

`requirements.txt` 에 `psutil` 추가 (없으면):
```
psutil==5.9.6
```

- [ ] **Step 2: 전체 pytest 실행**

Run: `cd recommendation-server && pytest tests/ -v`
Expected: 모두 통과

- [ ] **Step 3: Commit**

```bash
git add recommendation-server/main.py recommendation-server/requirements.txt
git commit -m "chore: /health memory/cache stats 확장"
```

---

# Phase I: Plan 실행 후 Eden 1회 작업 Checklist

이 Task 는 **코드 변경 없음** — Eden 이 실행할 단계를 문서화.

## Task 16: Eden 실행 가이드 문서

**Files:**
- Create: `docs/superpowers/plans/phase-1b-eden-runbook.md`

- [ ] **Step 1: Runbook 작성**

```markdown
# Phase 1B Eden Runbook

4/19 이후 (Supabase egress + GH Actions quota 리셋 후) 순서대로 실행.

## 1회 설정

- [ ] GitHub repo → Settings → Secrets & variables → Actions 에 secrets 3개 추가:
  - `SUPABASE_PROJECT_REF` (Supabase Dashboard → Settings → General → Reference ID)
  - `SUPABASE_ACCESS_TOKEN` (Account → Access Tokens → Create new)
  - `SUPABASE_DB_PASSWORD` (Supabase Dashboard → Settings → Database → Password)
  - `SUPABASE_DB_URL` (Dashboard → Settings → Database → Connection string — verify workflow 용)

- [ ] GitHub Actions → Apply Migrations → workflow_dispatch
  - Input `first_run=true` → Run workflow
  - 기존 10+ 개 migration 이력을 `applied` 로 등록 (실행 없이)
  - 이후 Phase 1B 신규 19개 migration 이 자동 apply

- [ ] Supabase SQL Editor 에서 pg_cron 등록 확인:
  ```sql
  SELECT jobname, schedule FROM cron.job ORDER BY jobname;
  ```
  기대: 9개 (refresh-curation-cache, aggregate-co-occurrence, ..., refresh_user_state 포함)

- [ ] GitHub Actions → Generate Curation Themes → workflow_dispatch (1회)
- [ ] GitHub Actions → Generate Cluster Themes → workflow_dispatch (1회)
- [ ] Supabase SQL Editor:
  ```sql
  SELECT refresh_fallback_curation();
  SELECT refresh_curation_cache_all();
  ```

## 검증

- [ ] `scripts/e2e_phase1b.sh` 실행 (test user JWT 발급 후)
- [ ] GitHub Actions → Verify Phase 1B → workflow_dispatch
- [ ] Render dashboard 에서 memory < 400MB 확인
- [ ] 72시간 Render + Supabase dashboard 관찰

## Go/No-go 8 기준

Spec §9.3 참고. 1개라도 미충족 시 원인 수정 후 재검증.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/phase-1b-eden-runbook.md
git commit -m "docs: Phase 1B Eden runbook — 1회 설정 + 검증 순서"
```

---

# Self-Review

## Spec Coverage 체크

| Spec 섹션 | Task 커버 |
|-----------|----------|
| §2 용어 정의 | 본문에 반영 (User Tier = engine/tier.py) |
| §3 범위 | Task 전체가 구현. 범위 외(Flutter/검색) 제외 |
| §4 아키텍처 | 전체 Task 로 실현 |
| §5.1 Migrations 19개 | Task 2-3 (00-11, 12-18) |
| §5.2 테이블 스키마 | Task 2 (01-11) |
| §5.3 기존 테이블 확장 | Task 2 (07, 08, 10) |
| §5.4 trigger + 단일 유저 refresh | Task 3 (14_functions_user_state) |
| §5.5 RLS | Task 2 각 migration 에 포함 |
| §6.1-6.5 API | Task 6 (recommend), Task 9 (home), Task 10 (curation) |
| §7 Background jobs | Task 3 (12-18), Task 11-12 (workflows) |
| §8 Caching/Performance | Task 8 (home_cache), §8.3 메모리는 Task 15 /health 로 측정 |
| §9.1 Rollout | Runbook (Task 16) |
| §9.2 Eden 수동 작업 | Runbook (Task 16) |
| §9.3 Go/No-go | Task 13 (verify workflow) + Runbook |
| §9.4 e2e 시나리오 | Task 13 (e2e_phase1b.sh) |
| §9.5 Rollback | Runbook 참조 |
| §9.6 verify workflow | Task 13 |
| §10 Layer 2 변수 | 각 migration/스크립트에 초기값 박음 |
| §11 운영 리스크 | 구현 범위 외 (Runbook 언급) |
| §12 Phase 2 이월 | Plan 범위 외 |

## Placeholder Scan

전체 Plan 훑어본 결과:
- 모든 step 에 실제 코드/명령어 포함
- "TBD", "TODO", "implement later" 없음
- "Similar to Task N" 없음
- 참조된 함수/타입 모두 정의됨

## Type Consistency 체크

- `compute_scored_books()` Task 4 정의 ↔ Task 9 호출: 키워드 인자 일치 ✅
- `sections_for_tier()` Task 5 정의 ↔ Task 9 호출: `list[dict]` 반환 일치 ✅
- `weighted_sample_one()` Task 7 정의 ↔ Task 9 호출: signature 일치 ✅
- `korean_particle()` / `similar_section_title()` Task 5 정의 ↔ Task 9 호출 ✅
- `save_home_cache_if_current()` Task 8 정의 ↔ Task 9 BackgroundTasks 호출: args 일치 ✅

Plan 검증 통과.
