# Phase 1B — 온보딩/홈 백엔드 설계

> 2026-04-15 | Eden
> 관련 spec: `2026-04-07-recommendation-algorithm-design.md`, `2026-04-07-curation-system-design.md`, `2026-04-07-data-collection-design.md`, `2026-04-13-recommendation-serving-optimization-design.md`
> 관련 메모리: Phase 1A 완료(2026-04-08 PR#2 머지), 서빙 최적화 완료(2026-04-14)

## 1. 배경 및 목표

Phase 1A(데이터 인프라)와 서빙 최적화가 완료된 상태에서, Phase 1B는 **출시 시점 유저가 홈을 열었을 때 보는 경험**을 완성한다.

현재 `/recommend` 엔드포인트는 Tier 구분 없이 H10 추천을 반환하고, 큐레이션 시스템은 없다. 스펙 3종이 요구하는 Tier 분기, 자동 큐레이션 풀, co-occurrence 인프라를 구현한다.

이 spec은 **서버 백엔드** 범위다. Flutter 앱 변경은 별도 product spec.

## 2. 용어 정의

세 가지 "tier" 개념이 공존하므로 명확히 구분한다.

| 용어 | 범위 | 값 | 정의 |
|------|------|-----|------|
| **User Tier** | 유저별 | 0/1/2 | 좋아요 수 기준 버킷 (Tier 0 < 3, Tier 1 3-5, Tier 2 ≥ 6) |
| **System Stage** | 시스템 전역 | 0/1/2/3 | 알고리즘 단계 (Cold → Warm 전환) |
| **Book data tier** | 책별 | 1/2 | 임베딩 데이터 단계 (Tier 1 기본 메타 / Tier 2 YES24 enriched) |

spec 본문에서 혼란 방지 위해 "User Tier", "System Stage" 형태로 명시한다.

## 3. 범위

### 3.1 포함

- User Tier 분기 (`/recommend` + 신규 `/home`)
- 큐레이션 자동 생성 4타입 (genre_combo, author, keyword, cluster)
- 큐레이션 풀 + 캐시 + 개인화 + 7일 노출 디스카운트
- co-occurrence 테이블 + 일일 집계
- System Stage 자동 전환 + CTR 기반 롤백 (Stage 0 고정 상태로 출시)
- user_state 실시간 갱신 (trigger)
- search_logs 테이블 (검색 기능 자체는 범위 외)
- impression curation_id 연결
- Migration 자동 적용 인프라

### 3.2 범위 외

- **Flutter 앱 변경** — 별도 product spec
- **검색 API** — 별도 spec (search_logs 테이블만 선제 준비)
- **impression batch INSERT API** — Flutter 변경 필요, Phase 2
- **CTR 대시보드 / alert** — Phase 2
- **Redis 캐시** — 부하 실측 후 Phase 2
- **A/B 테스트 프레임워크** — 스펙 명시 대로 X
- **소셜 신호 (친구 활동)** — Phase 2+

### 3.3 Phase 1B 완료 시 유저 가시 변화

**없음.** Flutter 앱이 여전히 `/recommend`만 호출하는 상태라 유저에게 보이는 UI는 이전과 동일. Phase 1B는 **Flutter 마이그레이션이 발현시킬 수 있는 서버 인프라 완성**이 목표.

## 4. 아키텍처

### 4.1 전체 흐름

```
[Flutter 앱]  (기존: /recommend 호출. 이후 product spec에서 /home 으로 전환)
    ↓ GET /home/{user_id}            (신규)
    ↓ GET /recommend/{user_id}       (기존 + Tier 2 체크)
    ↓ GET /similar/{book_id}         (기존)
    ↓ POST /similar/union            (기존)
    ↓ POST /feedback                 (기존)
    ↓ GET /curations/{id}/books      (신규)

[FastAPI / Render]
  app.state (L1 cache):
    index, books_meta, desc_matrix_f16, agg_reason_matrix_f16, prestacked_reasons_f16
  api/
    home.py        NEW
    recommend.py   기존 + User Tier 분기
    similar.py     기존
    curation.py    NEW
    feedback.py    기존
  engine/
    recommend_core.py   NEW (기존 scoring 추출)
    tier.py             NEW
    curation.py         NEW
    twostage.py         기존
    loader.py           기존 (v4 bundle 유지)

[Supabase]
  신규 테이블: curation_themes, curation_cache, user_curation_history,
              book_co_occurrence, search_logs, recommendation_stage,
              stage_transitions, book_cluster_assignments, home_section_cache
  확장 테이블: user_state (+top_authors, top_l1s),
              recommendation_impressions (+curation_id, idx_shown_at),
              books (+l1, l2 generated columns)
  신규 함수: refresh_user_state_single, refresh_user_top_taste_*,
              refresh_curation_cache_all, aggregate_co_occurrence,
              deactivate_curations, check_stage_transition,
              refresh_fallback_curation, cleanup_*
  신규 trigger: user_books AFTER INSERT/UPDATE/DELETE → user_state + top_taste
  신규 pg_cron jobs: 위 함수들 주기 실행

[GH Actions]
  apply-migrations.yml         신규 (on push)
  generate-curation-themes.yml 신규 (weekly Mon)
  generate-cluster-themes.yml  신규 (monthly 1st, LLM)
  daily-pipeline.yml           기존 (변경 없음)
  build-index.yml              기존
```

### 4.2 핵심 원칙

1. **읽기 경로는 in-memory 우선** — `/home` 응답은 app.state + Supabase 최소 쿼리
2. **쓰기 경로는 비동기** — BackgroundTasks로 cache write + impression INSERT
3. **집계는 pg_cron 최우선** — GH Actions 쿼터 + egress 절감
4. **반복 수동 작업 0** — 모든 주기 작업 cron/trigger
5. **기존 v4 index.pkl 구조 무변경** — 메모리 압박 + 배포 복잡도 회피
6. **Flutter 하위 호환** — `/recommend`는 Tier 2만 결과 반환, Tier 0/1은 빈 배열

## 5. 데이터 모델

### 5.1 Migration 파일 (19개, 자동 적용)

모두 `supabase/migrations/20260415_phase1b_XX_*.sql` 형식. PR 머지 시 `apply-migrations.yml`이 자동 적용.

| # | 파일 | 내용 |
|---|------|------|
| 00 | extensions.sql | `CREATE EXTENSION IF NOT EXISTS pg_cron` |
| 01 | curation_themes.sql | 테이블 + theme_key unique + RLS |
| 02 | curation_cache.sql | 테이블 + RLS |
| 03 | user_curation_history.sql | 테이블 + RLS |
| 04 | book_co_occurrence.sql | 테이블 + sparse 인덱스 + RLS |
| 05 | search_logs.sql | 테이블 + RLS |
| 06 | recommendation_stage.sql | singleton + thresholds JSONB + stage_transitions |
| 07 | user_state_extend.sql | top_authors/top_l1s JSONB 컬럼 추가 |
| 08 | books_genre_split.sql | l1/l2 generated column + 인덱스 |
| 09 | book_cluster_assignments.sql | 테이블 + RLS |
| 10 | impressions_extend.sql | curation_id 컬럼 + idx_shown_at 추가 |
| 11 | home_section_cache.sql | 테이블 + RLS |
| 12 | functions_curation.sql | refresh_curation_cache_all, deactivate_curations |
| 13 | functions_co_occurrence.sql | aggregate_co_occurrence |
| 14 | functions_user_state.sql | refresh_user_top_taste_*, trigger_refresh_user_state + trigger 등록 |
| 15 | functions_stage.sql | check_stage_transition |
| 16 | functions_fallback.sql | refresh_fallback_curation (SQL 재작성) |
| 17 | functions_cleanup.sql | cleanup_user_curation_history, cleanup_home_section_cache |
| 18 | cron_schedules.sql | 모든 pg_cron 등록 (idempotent wrap) |

### 5.2 테이블 주요 스키마

#### curation_themes (migration 01)

```sql
CREATE TABLE curation_themes (
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
  target_l1 TEXT, target_author TEXT, target_keyword TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_shown_at TIMESTAMPTZ,
  shown_count INT DEFAULT 0, click_count INT DEFAULT 0,
  click_rate FLOAT GENERATED ALWAYS AS (
    CASE WHEN shown_count > 0 THEN click_count::float / shown_count ELSE 0 END
  ) STORED
);
```

`theme_key`는 스펙에 없는 추가 필드. weekly 재생성 시 idempotent upsert를 위해 필요. 예: `"genre_combo|문학|한국 소설"`.

#### curation_cache (migration 02)

```sql
CREATE TABLE curation_cache (
  curation_id BIGINT PRIMARY KEY REFERENCES curation_themes(id) ON DELETE CASCADE,
  book_ids JSONB NOT NULL,
  cached_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);
```

hourly refresh. `expires_at = cached_at + 1 hour`.

#### user_curation_history (migration 03)

```sql
CREATE TABLE user_curation_history (
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  curation_id BIGINT NOT NULL REFERENCES curation_themes(id) ON DELETE CASCADE,
  shown_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (user_id, curation_id, shown_at)
);
```

30일 retention (monthly cleanup cron).

#### book_co_occurrence (migration 04)

```sql
CREATE TABLE book_co_occurrence (
  book_a_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  book_b_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  co_like_count INT DEFAULT 0,
  co_save_count INT DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (book_a_id, book_b_id),
  CHECK (book_a_id < book_b_id)
);

CREATE INDEX idx_co_a ON book_co_occurrence (book_a_id, co_like_count DESC)
  WHERE co_like_count >= 3;
CREATE INDEX idx_co_b ON book_co_occurrence (book_b_id, co_like_count DESC)
  WHERE co_like_count >= 3;
```

sparse (MIN_PAIR_COUNT=3 미만 제외).

#### recommendation_stage (migration 06)

```sql
CREATE TABLE recommendation_stage (
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
  pre_transition_ctr FLOAT, post_transition_ctr FLOAT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO recommendation_stage (id) VALUES (1);

CREATE TABLE stage_transitions (
  id BIGSERIAL PRIMARY KEY,
  from_stage INT NOT NULL, to_stage INT NOT NULL,
  reason TEXT CHECK (reason IN ('auto_promote','auto_rollback','manual')),
  active_user_count INT, co_pair_count INT,
  pre_ctr FLOAT, post_ctr FLOAT,
  transitioned_at TIMESTAMPTZ DEFAULT NOW()
);
```

thresholds는 JSONB로 운영 중 조정 가능 (UPDATE 쿼리).

#### books genre split (migration 08)

`books.genre`는 TEXT (형식: `"대분류>소분류"`). Python에서 split 처리 중이나 SQL 필터에 불편. generated column 추가.

```sql
ALTER TABLE books ADD COLUMN IF NOT EXISTS l1 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL THEN NULL ELSE split_part(genre, '>', 1) END
  ) STORED;
ALTER TABLE books ADD COLUMN IF NOT EXISTS l2 TEXT
  GENERATED ALWAYS AS (
    CASE WHEN genre IS NULL THEN NULL ELSE split_part(genre, '>', 2) END
  ) STORED;
CREATE INDEX idx_books_l1_l2 ON books (l1, l2) WHERE l1 IS NOT NULL;
```

#### home_section_cache (migration 11)

```sql
CREATE TABLE home_section_cache (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  sections JSONB NOT NULL,
  tier INT NOT NULL, stage INT NOT NULL,
  input_hash TEXT NOT NULL,
  computed_at TIMESTAMPTZ DEFAULT NOW()
);
```

무효화: `input_hash = sha256(user_state.updated_at + current_hour_bucket)`. 유저 행동(trigger로 user_state 갱신) 또는 시간 bucket 변경 시 miss.

### 5.3 기존 테이블 확장

| 테이블 | 추가 |
|--------|------|
| `user_state` | `top_authors JSONB`, `top_l1s JSONB` (default `'[]'`) |
| `recommendation_impressions` | `curation_id BIGINT FK`, `idx_imp_shown_at (shown_at)` 인덱스 |

### 5.4 단일 유저 refresh 함수 (migration 14)

```sql
CREATE OR REPLACE FUNCTION refresh_user_state_single(target_user_id UUID) ...
-- total_likes/saves/finished, last_active_at, is_active, current_tier 계산
-- 끝에서 PERFORM refresh_user_top_taste_single(target_user_id);

CREATE OR REPLACE FUNCTION refresh_user_top_taste_single(target_user_id UUID) ...
-- user_books JOIN books로 top 5 author, top 3 l1 계산 → user_state UPDATE

CREATE TRIGGER user_books_state_sync
  AFTER INSERT OR UPDATE OR DELETE ON user_books
  FOR EACH ROW EXECUTE FUNCTION trigger_refresh_user_state();
```

User Tier 즉시 전환 보장 (6번째 좋아요 순간 Tier 2).

### 5.5 RLS 정책 원칙

- `curation_*`: 공개 read (books 공개 정보의 묶음)
- `user_curation_history`, `home_section_cache`, `search_logs`: 본인 row만 read
- `book_co_occurrence`, `recommendation_stage`, `stage_transitions`: 공개 read
- `book_cluster_assignments`: 공개 read
- 모든 write: service_role (pg_cron, 워커)

## 6. API Surface

### 6.1 엔드포인트 목록

| Method | Path | 상태 | 용도 |
|--------|------|------|------|
| GET | `/home/{user_id}` | NEW | Tier별 섹션 조립 홈 응답 |
| GET | `/recommend/{user_id}` | 기존 + Tier 2 체크 | 개인 추천 (Flutter 하위 호환) |
| GET | `/similar/{book_id}` | 기존 | book-to-book |
| POST | `/similar/union` | 기존 | 온보딩 벡터 평균 |
| POST | `/feedback` | 기존 | 좋아요/싫어요 로깅 |
| GET | `/curations/{curation_id}/books` | NEW | 큐레이션 전체 책 페이징 |
| GET | `/health` | 기존 + 확장 | cache hit rate, memory 포함 |

### 6.2 GET /home/{user_id}

**Auth**: JWT, path user_id 일치 필수.

**Response** (Tier 2 예시):

```json
{
  "user_id": "uuid",
  "tier": 2,
  "stage": 0,
  "sections": [
    {"id": "recommend_primary", "type": "personal_recommend",
     "title": "당신을 위한 추천", "books": [...], "algorithm_version": "h10_stage0"},
    {"id": "curation_123", "type": "curation", "title": "무라카미 하루키 컬렉션",
     "description": "...", "curation_id": 123, "personalization": "by_author", "books": [...]},
    {"id": "similar_abc", "type": "similar", "title": "『노르웨이의 숲』과 비슷한 책",
     "seed_book_id": "abc", "books": [...]},
    {"id": "curation_456", "type": "curation", ...},
    {"id": "fallback_trending", "type": "trending", "title": "화제의 책", "books": [...]},
    {"id": "category_nav", "type": "category_nav", "books": []}
  ],
  "cta": null,
  "computed_at": "2026-04-15T10:00:00Z",
  "cache_hit": true
}
```

**Tier별 섹션 구성** (스펙 curation 6.2):

| Tier | 섹션 순서 |
|------|---------|
| 0 | trending → curation(general) → curation(general) → category_nav |
| 1 | similar → curation(by_author) → curation(by_l1) → curation(general) → category_nav |
| 2 | personal_recommend → curation(by_author) → similar → curation(tier2+) → trending |

`category_nav`는 books=[] 반환, Flutter가 정적 UI 처리.

**Fallback 규칙**:
- Tier 1 `by_author` 섹션에 매칭되는 큐레이션 없으면 → `general`로 대체
- Tier 1/2 `similar` 섹션의 seed book (최근 좋아한 책) 없으면 → `general` 큐레이션으로 대체
- curation_cache `expires_at` 지난 row skip (다음 refresh 대기)
- books_meta에 없는 책 id skip (섹션당 책 수 감소 허용)

**CTA 생성**:
```python
if tier == 0: cta = f"좋아요 {3 - total_likes}권 더 누르면 비슷한 책 추천이 시작돼요"
elif tier == 1: cta = f"좋아요 {6 - total_likes}권 더 평가하면 취향 추천이 시작돼요"
else: cta = None
```

**내부 동작**:
1. user_state 1-row read
2. home_section_cache read (hash 비교) — hit이면 sections 반환
3. miss면 섹션 조립 (engine/tier.py의 Tier별 로직)
4. books_meta 조립은 in-memory (app.state.books_meta)
5. 응답 return → BackgroundTasks로 cache write + impression INSERT

**impression 로깅 책임**:
- `/home`에서 노출된 책은 **서버 BackgroundTasks**가 `recommendation_impressions` INSERT (curation_id 포함)
- `/recommend`, `/similar` 직접 호출은 Flutter ImpressionLogger (기존 유지)
- 중복 방지: `/home` 내부의 recommend_core 호출 시 Flutter 로깅 불필요

**한국어 조사 처리**:
```python
def particle(word, with_bat, without_bat):
    last = word[-1]
    if '가' <= last <= '힣':
        return with_bat if (ord(last) - 0xAC00) % 28 != 0 else without_bat
    return without_bat

# similar title 생성
title = f"『{seed.title}』{particle(seed.title, '과', '와')} 비슷한 책"
```

### 6.3 GET /recommend/{user_id} — Tier 2 체크 추가

```python
if user_state.current_tier < 2:
    return {"recommendations": [], "reason": "insufficient_likes",
            "tier": user_state.current_tier}
# Tier 2만 기존 경로
```

Flutter 하위 호환: 빈 배열 받으면 UI에서 적절히 처리.

### 6.4 GET /curations/{curation_id}/books

**Query**: `offset` (default 0), `limit` (default 10, max 50)

**Response**:
```json
{
  "curation_id": 123,
  "theme_type": "keyword",
  "title": "자아 찾기",
  "description": "...",
  "total": 30,
  "offset": 0, "limit": 10,
  "books": [...],
  "cached_at": "2026-04-15T09:00:00Z"
}
```

Flutter "더보기" 탭용.

### 6.5 에러 경로

| 조건 | 처리 |
|------|-----|
| user_state row 없음 (신규 유저) | Tier 0 default |
| curation_cache 전체 empty | trending만 반환, 나머지 섹션 비움 |
| recommend_core 실패 (Tier 2) | recommend 섹션만 drop, 나머지 유지 |
| stage 조회 실패 | stage=0 default |
| books_meta miss (books 테이블엔 있지만 index에 없음) | 해당 책 skip, 섹션당 책 수 감소 |

## 7. Background Jobs

### 7.1 pg_cron (Supabase 내, egress 0, GH Actions 쿼터 0)

| 함수 | cron | 역할 |
|------|------|-----|
| `refresh_curation_cache_all` | `5 * * * *` (hourly) | theme.selection_query 실행 → curation_cache upsert |
| `aggregate_co_occurrence` | `0 17 * * *` (daily 02:00 KST) | user_books like 페어 full rebuild |
| `refresh_user_top_taste_all` | `15 17 * * *` (daily 02:15) | 활성 유저 top_authors/top_l1s 갱신 (safety net, trigger 보완) |
| `refresh_fallback_curation` | `30 17 * * *` (daily 02:30) | books top 30 by loan_count → fallback_curation |
| `deactivate_curations` | `45 17 * * *` (daily 02:45) | 30일 노출 0 / 90일 click_rate<0.5% → is_active=FALSE |
| `check_stage_transition` | `0 18 * * *` (daily 03:00) | 활성 유저 + co_pair 체크 → promote/rollback |
| `cleanup_user_curation_history` | `0 20 1 * *` (monthly 1st) | 30일 초과 삭제 |
| `cleanup_home_section_cache` | `0 20 15 * *` (monthly 15th) | 30일 초과 삭제 |
| `refresh_user_state` (기존) | hourly | Phase 1A 유지 (safety net) |

**pg_cron 등록 idempotent wrap**:
```sql
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname='refresh-curation-cache') THEN
    PERFORM cron.unschedule('refresh-curation-cache');
  END IF;
  PERFORM cron.schedule('refresh-curation-cache', '5 * * * *',
    'SELECT refresh_curation_cache_all()');
END $$;
```

### 7.2 GH Actions workflows

| 파일 | 주기 | 용도 |
|------|-----|------|
| `apply-migrations.yml` | on push (paths: `supabase/migrations/**`) | Supabase CLI로 자동 적용 |
| `generate-curation-themes.yml` | weekly Mon 04:30 KST | genre_combo/author/keyword theme 생성 (Python, rule-based) |
| `generate-cluster-themes.yml` | monthly 1st 05:30 KST | KMeans + OpenAI gpt-4o-mini (LLM 필수) |
| `daily-pipeline.yml` (기존) | daily | 변경 없음 |
| `build-index.yml` (기존) | on push | 변경 없음 |

**GH Actions 월 쿼터 추정**: 약 670/2,000 분 (충분).

### 7.3 pg_cron 함수 핵심 로직

#### refresh_curation_cache_all

```sql
FOR theme IN SELECT * FROM curation_themes WHERE is_active=TRUE LOOP
  BEGIN
    CASE theme.theme_type
      WHEN 'genre_combo' THEN
        SELECT array_agg(id ORDER BY loan_count DESC NULLS LAST) INTO book_ids
        FROM (SELECT id FROM books
              WHERE l1 = theme.parameters->>'l1' AND l2 = theme.parameters->>'l2'
              ORDER BY loan_count DESC NULLS LAST LIMIT theme.max_books) s;
      WHEN 'author' THEN ...
      WHEN 'keyword' THEN
        -- books.library_keywords TEXT[] @> ARRAY[keyword]
      WHEN 'cluster' THEN
        -- book_cluster_assignments JOIN filter
    END CASE;

    IF array_length(book_ids, 1) >= theme.min_books THEN
      INSERT INTO curation_cache (...) ON CONFLICT (curation_id) DO UPDATE ...;
    ELSE
      UPDATE curation_themes SET is_active=FALSE WHERE id = theme.id;
    END IF;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Failed theme %: %', theme.id, SQLERRM;
    CONTINUE;
  END;
END LOOP;
```

#### aggregate_co_occurrence

Full rebuild (현재 스케일에서 단순성 우위).

```sql
CREATE TEMP TABLE tmp_co ON COMMIT DROP AS
  SELECT LEAST(a.book_id, b.book_id) AS book_a_id,
         GREATEST(a.book_id, b.book_id) AS book_b_id,
         COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') AS co_like_count,
         COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') AS co_save_count
  FROM user_books a JOIN user_books b
    ON a.user_id = b.user_id AND a.book_id < b.book_id
  WHERE (a.rating='good' OR a.status='wishlist') AND (b.rating='good' OR b.status='wishlist')
  GROUP BY 1,2
  HAVING COUNT(*) FILTER (WHERE a.rating='good' AND b.rating='good') >= 3
      OR COUNT(*) FILTER (WHERE a.status='wishlist' AND b.status='wishlist') >= 3;

DELETE FROM book_co_occurrence;
INSERT INTO book_co_occurrence SELECT *, NOW() FROM tmp_co;
```

#### check_stage_transition

7일 경과 조건은 promote/rollback 양쪽 적용:

```sql
-- Promote 조건
IF v_cfg ? v_next_key AND v_entered < NOW() - INTERVAL '7 days' THEN
  IF v_users >= (v_cfg->v_next_key->>'users')::INT
     AND v_pairs >= (v_cfg->v_next_key->>'pairs')::INT THEN
    -- pre_ctr 계산
    SELECT (COUNT(*) FILTER (WHERE action IN ('clicked','liked','saved'))::float
            / NULLIF(COUNT(*), 0))
    INTO v_pre_ctr
    FROM recommendation_impressions WHERE shown_at >= v_entered;

    UPDATE recommendation_stage
      SET current_stage = v_next_stage, entered_at = NOW(),
          pre_transition_ctr = v_pre_ctr, post_transition_ctr = NULL,
          updated_at = NOW()
      WHERE id=1;
    INSERT INTO stage_transitions (...) VALUES (..., 'auto_promote', ..., v_pre_ctr);
  END IF;
END IF;

-- Rollback 조건 (v_stage > 0 AND 7일 경과 AND pre_ctr 있음 AND 1000+ 노출 AND CTR -20%↓)
```

### 7.4 GH Actions 스크립트

#### generate_curation_themes.py (weekly)

```python
# rule-based, idempotent upsert by theme_key
# genre_combo: books GROUP BY (l1, l2) HAVING COUNT >= 10
# author: books GROUP BY author HAVING COUNT >= 3
# keyword: unnest(library_keywords) GROUP BY keyword HAVING COUNT >= 5
```

#### generate_cluster_themes.py (monthly)

```python
# 1. index.pkl을 git LFS pull (Supabase egress 0)
# 2. KMeans(desc_matrix_f16, n_clusters=30)
# 3. book_cluster_assignments upsert (cluster_version='v202605')
# 4. 각 cluster 대표 책 5개 → OpenAI gpt-4o-mini로 title/description 생성
#    - 검증: title 5~30자, 금지어 필터
#    - fallback: rule-based template title
# 5. curation_themes (theme_type='cluster', theme_key='cluster|{cluster_id}') upsert
#    - parameters.cluster_version 갱신
# 6. 전체 트랜잭션 묶음

# feedback_batch_operations 준수:
# - OpenAI rate limit용 sleep
# - per-cluster try/except
# - 각 cluster commit
# - --dry-run 플래그
# - 생성 count 로깅
```

LLM 비용 추정: 30 clusters × ($0.15 × 500 + $0.6 × 100) / 1M = 월 $0.004.

#### apply-migrations.yml

```yaml
name: Apply Migrations
on:
  push:
    branches: [main]
    paths: ['supabase/migrations/**']
  workflow_dispatch:
    inputs:
      first_run:
        description: 'Bootstrap: register existing migrations as applied'
        required: false
        default: 'false'

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: supabase/setup-cli@v1
      - run: supabase link --project-ref ${{ secrets.SUPABASE_PROJECT_REF }}
        env:
          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
      - if: ${{ github.event.inputs.first_run == 'true' }}
        run: |
          # 기존 migration을 applied로 repair
          for f in supabase/migrations/*.sql; do
            v=$(basename "$f" .sql | awk -F_ '{print $1}')
            supabase migration repair --status applied "$v" || true
          done
      - run: supabase db push --password '${{ secrets.SUPABASE_DB_PASSWORD }}'
```

## 8. Caching + Performance

### 8.1 캐시 계층

| Layer | 저장소 | 무효화 | 내용 |
|-------|-------|------|------|
| L1 app.state | Render 메모리 | 서버 재시작 (deploy 단위) | index, books_meta, 3개 matrix |
| L2 recommendation_cache | Postgres | `input_hash = f(user_books)` | /recommend 결과 Top 50 |
| L3 home_section_cache | Postgres | `input_hash = sha256(user_state.updated_at + hour_bucket)` | /home 응답 전체 |
| L4 curation_cache | Postgres | `expires_at` (1시간) | 큐레이션 book_ids |

### 8.2 성능 목표

| Endpoint | Cache Hit | Cache Miss |
|----------|-----------|-----------|
| `/home` (L3 hit) | < 30ms | - |
| `/home` (miss, Tier 0/1) | - | < 100ms |
| `/home` (miss, Tier 2) | - | < 300ms |
| `/recommend` (L2 hit) | < 30ms | - |
| `/recommend` (miss) | - | < 500ms |
| `/similar/{book_id}` | < 50ms | - |
| `/curations/{id}/books` | < 30ms | - |

### 8.3 메모리 예산 (Render Free 512MB)

| 항목 | MB |
|------|-----|
| Python + FastAPI + deps | ~100 |
| desc_matrix_f16 (9,443 × 2000 × 2B) | ~38 |
| agg_reason_matrix_f16 | ~38 |
| prestacked_reasons_f16 (42,849 × 2000 × 2B) | ~171 |
| books_meta + index structs | ~10 |
| **기본 상주** | **~357** |
| 동시 요청 3개 피크 (numpy matmul copy) | +120 |
| **최악 피크** | **~477** |

Free tier 512MB 여유 30MB. 동시 요청 증가 시 Starter($7) 이동. **메모리 모니터링 필수**.

### 8.4 Egress 추정 (활성 500명)

| 경로 | MB/day | MB/month |
|------|--------|---------|
| `/home` (16KB × 1,500) | 24 | 720 |
| `/recommend` 기존 (cache hit 80%) | 5 | 150 |
| impression (현재 non-batch) | 22.5 | 675 |
| co_occurrence daily | 0 (pg_cron, DB 내부) | 0 |
| curation_cache hourly | 0 (pg_cron) | 0 |
| build_index 증분 (기존) | - | 1,200 |
| **Phase 1B 합계** | | **~2.9 GB** |

Supabase Free 5.5GB/월 한도 내. 출시 초기 활성 ~50명은 훨씬 적음.

### 8.5 최적화 구현 사항

1. **`/home` curation 조회 IN clause 통합**: 5 query → 1 query
2. **impression BackgroundTasks batch INSERT**: 60 rows × 단일 SQL
3. **books_meta miss = skip**: 섹션당 책 수 감소 허용 (N+1 회피)
4. **stage 매 호출 조회**: 1 row ~100B, 캐싱 복잡도 회피

### 8.6 모니터링 (출시 초기 최소)

- `GET /health` 확장: `memory_mb`, `cache_hits`, `cache_misses` (프로세스 counter)
- `SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20`
- FastAPI structured logging (request latency, cache hit/miss)
- Render dashboard (메모리, CPU, latency)
- Supabase dashboard (egress, query time)

## 9. Rollout + Verification

### 9.1 실행 단계

```
Phase 1B.0 — 개발 (현재 ~ 4/19)
  ├─ 19 migration files + plpgsql 함수
  ├─ recommendation-server/ 신규 Python 파일
  ├─ 3개 신규 GH Actions workflow
  └─ PR 리뷰

Phase 1B.1 — Eden 1회 설정 (4/19 이후)
  ├─ GH secrets 3개 (SUPABASE_PROJECT_REF, ACCESS_TOKEN, DB_PASSWORD)
  ├─ apply-migrations workflow_dispatch --first-run
  └─ pg_cron 등록 확인 (SELECT * FROM cron.job)

Phase 1B.2 — 자동 배포
  ├─ PR merge → apply-migrations 자동 → DB 스키마 적용
  ├─ daily-pipeline build-index → index.pkl 갱신
  └─ Render auto-deploy

Phase 1B.3 — 초기 seed (Eden 1회)
  ├─ generate_curation_themes workflow_dispatch
  ├─ generate_cluster_themes workflow_dispatch
  ├─ SELECT refresh_fallback_curation() 1회
  └─ SELECT refresh_curation_cache_all() 1회

Phase 1B.4 — 검증
  ├─ pytest (CI)
  ├─ verify-phase-1b.yml (자동 검증 항목)
  ├─ locust load test (off-peak)
  ├─ end-to-end 시나리오 (Eden curl)
  └─ 72시간 dashboard 모니터링

Phase 1B.5 — Flutter 마이그레이션 (별도 product spec)
```

### 9.2 Eden 1회 수동 작업 목록

- [ ] GitHub repo secrets 3개 추가
- [ ] `workflow_dispatch: apply-migrations --first-run`
- [ ] `workflow_dispatch: generate_curation_themes`
- [ ] `workflow_dispatch: generate_cluster_themes`
- [ ] `SELECT refresh_fallback_curation()` (Supabase SQL editor)
- [ ] `SELECT refresh_curation_cache_all()` (Supabase SQL editor)
- [ ] End-to-end 검증 시나리오 실행
- [ ] 72시간 Render + Supabase dashboard 관찰
- [ ] Go/No-go 판정

**이후 반복 수동 작업 없음.**

### 9.3 Go/No-go 기준

| # | 기준 | 측정 |
|---|------|------|
| 1 | pytest 모두 pass | CI |
| 2 | `/home` 4-case (Tier 0/1/2/신규) 정상 응답 | curl end-to-end 시나리오 |
| 3 | Render 메모리 < 400MB 상주 | Render dashboard |
| 4 | `/home` p95 < 300ms (동시 10 요청) | locust |
| 5 | pg_cron 모든 job 등록 + 24h 실행 기록 | `cron.job_run_details` SQL |
| 6 | trigger 기반 User Tier 즉시 전환 (test user) | SQL 수동 검증 |
| 7 | impression.curation_id 정상 기록 | SQL COUNT |
| 8 | Supabase egress < 활성 유저 × 0.3 MB/day | Supabase dashboard |

1개라도 미충족 → 원인 수정 후 재검증.

### 9.4 End-to-end 검증 시나리오 (Eden curl)

```bash
# 0. 환경 변수
export API=https://curation-recommendation.onrender.com
export JWT=<test user JWT>
export UID=<test user id>

# 1. 신규 유저 /home 호출 (Tier 0 기대)
curl -H "Authorization: Bearer $JWT" "$API/home/$UID" | jq '.tier, .sections[].type'
# 기대: tier=0, sections=[trending, curation, curation, category_nav]

# 2. user_books에 좋아요 3권 주입 (Supabase)
# ... INSERT INTO user_books (user_id, book_id, rating, status) VALUES (...)
# trigger로 user_state 즉시 갱신

# 3. /home 재호출 (Tier 1 기대)
curl -H "Authorization: Bearer $JWT" "$API/home/$UID" | jq '.tier, .sections[].type'
# 기대: tier=1, sections=[similar, curation(by_author), curation(by_l1), ...]

# 4. 좋아요 6권 추가 (총 9권)
# 5. /home 재호출 (Tier 2, personal_recommend 등장)
curl -H "Authorization: Bearer $JWT" "$API/home/$UID" | jq '.tier, .sections[].type'
# 기대: tier=2, sections=[personal_recommend, curation(by_author), similar, ...]

# 6. curation impression 로깅 확인
# SELECT COUNT(*) FROM recommendation_impressions WHERE user_id='$UID' AND curation_id IS NOT NULL;
# 기대: > 0
```

### 9.5 Rollback

- **Render**: 이전 배포로 1-click revert (5분)
- **Migration**: additive 원칙. DROP 필요 시 `supabase/migrations/rollback/` 에 revert SQL 보관
- **pg_cron**: `SELECT cron.unschedule('job-name')`

### 9.6 verify-phase-1b.yml (자동 검증 워크플로우)

```yaml
on: workflow_dispatch
jobs:
  verify:
    steps:
      - uses: actions/checkout@v4
      - run: pytest recommendation-server/tests
      - run: |
          psql $DB_URL -c "SELECT jobname FROM cron.job" | \
            grep -E "refresh-curation-cache|aggregate-co-occurrence|..."
      - run: |
          psql $DB_URL -c "SELECT COUNT(*) FROM recommendation_impressions WHERE curation_id IS NOT NULL"
```

## 10. Layer 2 변수 (출시 후 조정)

| 항목 | 초기값 | 저장 위치 | 조정 시점 |
|------|-------|---------|---------|
| TIER1_THRESHOLD | 3 | plpgsql (refresh_user_state_single) | 출시 30일 후 |
| TIER2_THRESHOLD | 6 | 같음 | 출시 30일 후 |
| Stage 1~3 임계 (users, pairs) | 100/200, 300/1K, 500/3K | `recommendation_stage.thresholds` | 실측 |
| ROLLBACK_THRESHOLD | -0.2 | 같음 | 첫 stage 전환 후 |
| min_exposure | 1000 | 같음 | 실측 |
| MIN_PAIR_COUNT | 3 | aggregate_co_occurrence SQL | Stage 1 진입 후 |
| MIN_BOOKS_GENRE_COMBO | 10 | generate_curation_themes.py | 큐레이션 수 실측 |
| MIN_AUTHOR_BOOKS | 3 | 같음 | 같음 |
| MIN_KEYWORD_BOOKS | 5 | 같음 | 같음 |
| 큐레이션 생성 주기 | weekly | GH Actions cron | 신선도 실측 |
| cluster 수 | 30 | generate_cluster_themes.py | 책 수 증가 시 |
| curation_cache TTL | 1 hour | refresh_curation_cache_all | 부하 실측 |
| 신간 가중치 | 0.05 | curation 선택 로직 | 클릭 실측 |
| 개인화 가중치 (by_*) | 2.0 | curation sampling | 실측 |

## 11. 운영 리스크 (범위 외 대응)

- **Render Free cold start** (15분 비활성 → 재시작 10~30초): Starter $7 업그레이드 or UptimeRobot ping
- **OOM 위험** (동시 요청 3+ 시 512MB 근접): Starter 이동 + worker 조정
- **Supabase 502** (egress/quota 초과): billing cycle 리셋 대기, 4/19 복구 확인 필수
- **GH Actions quota 초과** (현재 초과 상태): 4/19 리셋 대기
- **OpenAI API 실패** (cluster 생성): fallback rule-based title, 다음 월 재시도
- **Sentry/alert 없음**: Phase 2
- **staging 환경 없음**: off-peak 검증으로 대체
- **plpgsql unit test 프레임워크 부재**: 수동 시나리오 검증

## 12. Phase 2 이월 항목

- Flutter `/home` 마이그레이션 (별도 product spec)
- impression batch INSERT API (앱 변경 필요)
- 검색 기능 + API (search_logs는 선제 준비됨)
- CTR 대시보드 / alert (Slack/email)
- Redis 캐시 (부하 실측 후)
- co_occurrence 증분 집계 (활성 5,000명+ 시점)
- feature flag 시스템
- cluster 개수 자동 조정
- plpgsql test 프레임워크
- Render staging 환경

## 13. 핵심 원칙

1. **스펙 3종 전면 준수** (algorithm/curation/data 4/07)
2. **기존 v4 index.pkl 구조 무변경** (메모리/배포 리스크 회피)
3. **반복 수동 작업 0** (pg_cron + trigger + workflow)
4. **egress + 메모리 수치 검증 후 설계** (Eden 원칙)
5. **범위 축소 없음** (Eden 결정 "스펙 전체 구현" 존중)
6. **Flutter 변경 분리** (product spec으로 이관)
7. **Layer 2 변수는 DB JSONB로** (재배포 없이 조정 가능)
8. **모든 DDL은 migration 파일** (feedback_no_direct_sql)

## 14. 출시 후 결정 항목

| 항목 | 측정 시점 |
|------|---------|
| home_section_cache hit rate | 출시 후 1주 |
| Tier 전환율 (0→1, 1→2) | 출시 후 30일 |
| 큐레이션 type별 CTR | 출시 후 30일 |
| Stage 전환 임계 적정성 | 활성 100명 근접 시 |
| 메모리 피크 실측 | 출시 후 1주 |
| egress 실측 | 출시 후 1주 |
| OpenAI 비용 실측 | 첫 cluster 생성 후 |
| 큐레이션 신선도 (평균 노출/30일 미노출 비율) | 출시 후 30일 |
