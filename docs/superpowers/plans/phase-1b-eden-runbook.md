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
