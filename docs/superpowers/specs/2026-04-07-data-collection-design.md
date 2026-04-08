# 데이터 수집 인프라 설계

> 2026-04-07 | Eden
> 관련 spec: `2026-04-07-recommendation-algorithm-design.md`, `2026-04-07-curation-system-design.md`

## 1. 배경

추천 알고리즘이 Cold(content) → Warm(Hybrid CF) 로 전환되려면 유저 행동 데이터가 누적되어야 한다. **첫날부터 수집을 시작해야** 활성 100명 시점에 의미 있는 데이터가 있다. 또한 운영 후 임계값/가중치 조정을 위해서도 데이터가 필요.

## 2. 수집 항목 (Eden 결정 8개)

| # | 항목 | 목적 | 우선순위 |
|---|---|---|---|
| 1 | 노출 로그 (impression) | 추천 → 유저 행동 측정. CF 학습 데이터 | P0 |
| 2 | 노출 후 액션 | clicked/saved/liked/disliked/ignored | P0 |
| 3 | 재추천 횟수 | 같은 user×book 노출 카운트 | P0 |
| 4 | wishlist → bad 전환 | 관심 → 실망 신호 (강한 학습 데이터) | P0 |
| 5 | 좋아요와 저장 분리 | 명확히 다른 신호 (좋아요=평가, 저장=관심) | P0 |
| 6 | 검색 쿼리 | 명시적 의도 신호 | P1 |
| 7 | 세션 패턴 | 추천 → 행동까지 시간 (즉시성 측정) | P1 |
| 8 | 연속 추천 거부 | 알고리즘 reset 트리거 데이터 | P1 |
| 9 | 공동 좋아요 (co-occurrence) | CF의 핵심 데이터 (자동 집계) | P0 |

**제외**: 읽기 후 평가 변화 / 체류 시간 / 공유 / 시간대 (Eden 의견)

## 3. 데이터 모델

### 3.1 user_books 상태 정리 (기존 테이블 변경)

현재: `rating` 컬럼만 (good/neutral/bad)

신규: status + rating 분리

```
status    : wishlist | reading | finished
rating    : null | good | bad
```

| status | 의미 | rating 가능 값 |
|---|---|---|
| wishlist | 읽고 싶음 (저장) | null만 |
| reading | 읽는 중 | null/good/bad |
| finished | 완독 | null/good/bad |

**status와 rating은 독립적으로 변경 가능**. 유저가 wishlist에서 바로 finished+bad로 갈 수도 있음 (산 것 후회).

### 3.2 신규 테이블 (5개)

**확장성과 성능을 위해 인덱스 + partition 고려.**

#### 3.2.1 recommendation_impressions

추천 노출 + 유저 행동 추적.

```sql
CREATE TABLE recommendation_impressions (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL,
  position INT NOT NULL,                  -- Top N에서의 순위
  source TEXT NOT NULL,                   -- 'home_recommend' / 'similar' / 'curation' / 'search'
  algorithm_version TEXT NOT NULL,        -- 'h10_stage0' / 'hybrid_stage1' 등
  shown_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  action TEXT,                            -- 'clicked' / 'saved' / 'liked' / 'disliked' / null
  action_at TIMESTAMPTZ,
  session_id TEXT                         -- 같은 세션 내 그룹화
);

CREATE INDEX idx_imp_user_time ON recommendation_impressions (user_id, shown_at DESC);
CREATE INDEX idx_imp_book ON recommendation_impressions (book_id);
CREATE INDEX idx_imp_unactioned ON recommendation_impressions (user_id, shown_at DESC) WHERE action IS NULL;
```

**Partition (확장성)**: 데이터가 누적되면 월별 partition 고려 (Postgres native 또는 timescaledb).

**비고**:
- impression 발생 시 즉시 INSERT (lightweight)
- 액션 발생 시 UPDATE로 action/action_at 채움
- 재추천 횟수 = 같은 user_id+book_id의 row 수 = 별도 카운터 불필요

#### 3.2.2 book_co_occurrence

책 페어 카운트. CF 핵심.

```sql
CREATE TABLE book_co_occurrence (
  book_a_id UUID NOT NULL,
  book_b_id UUID NOT NULL,
  co_like_count INT DEFAULT 0,           -- 함께 좋아요 받은 횟수
  co_save_count INT DEFAULT 0,           -- 함께 저장된 횟수
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (book_a_id, book_b_id),
  CHECK (book_a_id < book_b_id)          -- 양방향 중복 방지
);

CREATE INDEX idx_co_a ON book_co_occurrence (book_a_id, co_like_count DESC);
CREATE INDEX idx_co_b ON book_co_occurrence (book_b_id, co_like_count DESC);
```

**갱신**: 매일 새벽 백그라운드 워커가 신규 좋아요/저장에서 페어 추출 → upsert.

**확장성**: 책 풀이 커지면 row 수 증가 (N²). 2,651권 기준 최대 ~3.5M row. **모든 페어가 아닌 `co_like_count ≥ MIN_PAIR_COUNT`만 유지** (sparse).

#### 3.2.3 search_logs

검색 쿼리 + 결과.

```sql
CREATE TABLE search_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID,
  query TEXT NOT NULL,
  result_count INT NOT NULL,
  clicked_book_id UUID,
  searched_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_search_user ON search_logs (user_id, searched_at DESC);
CREATE INDEX idx_search_query ON search_logs USING gin (to_tsvector('simple', query));
```

#### 3.2.4 user_books_history

user_books 변경 이력. wishlist → bad 추적.

```sql
CREATE TABLE user_books_history (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL,
  old_status TEXT,
  new_status TEXT,
  old_rating TEXT,
  new_rating TEXT,
  changed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_history_user_book ON user_books_history (user_id, book_id, changed_at DESC);
```

**자동 채움**: user_books에 BEFORE UPDATE trigger.

```sql
CREATE TRIGGER user_books_audit
BEFORE UPDATE ON user_books
FOR EACH ROW EXECUTE FUNCTION log_user_books_change();
```

#### 3.2.5 user_state

유저별 집계 캐시 (실시간 계산 회피).

```sql
CREATE TABLE user_state (
  user_id UUID PRIMARY KEY,
  total_likes INT DEFAULT 0,
  total_saves INT DEFAULT 0,
  total_finished INT DEFAULT 0,
  consecutive_ignores INT DEFAULT 0,      -- 연속 무시 카운터
  last_active_at TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT FALSE,        -- 30일 내 활동 여부
  current_tier INT DEFAULT 0,             -- 0/1/2
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**갱신**: 유저 액션 시 trigger 또는 매시간 batch.

## 4. 백그라운드 작업

| 작업 | 주기 | 입력 | 출력 |
|---|---|---|---|
| co_occurrence 집계 | 매일 02:00 | user_books 신규 like/save | book_co_occurrence upsert |
| user_state 갱신 | 매시간 | user_books, recommendation_impressions | user_state row |
| 활성 유저 카운트 | 매일 | user_state | metric (대시보드) |
| Stage 전환 트리거 | 매일 | 활성 유저 + co_pair 카운트 | 알고리즘 spec의 stage 변경 |
| Cache 갱신 | 알고리즘 spec 참조 | - | - |

## 5. 로딩 성능 영향

데이터 수집은 **모두 비동기 + lightweight INSERT**. 유저 응답에 영향 0.

- impression INSERT: < 5ms
- action UPDATE: < 5ms
- 백그라운드 집계: 유저 응답 경로 밖

## 6. 확장성 고려

### 데이터 증가 예상

| 시점 | 활성 유저 | impressions/일 | history rows | co_occurrence rows |
|---|---|---|---|---|
| 출시 | 0 | 0 | 0 | 0 |
| 활성 100 | 100 | ~2,000 | ~500 | ~1,000 |
| 활성 500 | 500 | ~10,000 | ~2,500 | ~10,000 |
| 활성 5,000 | 5,000 | ~100,000 | ~25,000 | ~100,000 |

**Postgres 단일 노드로 충분.** 활성 만 명+ 시점에 partition 도입 검토.

### Index 부담

- impression INSERT 빈도 높음 → 인덱스 4개로 최소화
- 분석 쿼리는 read replica 또는 별도 OLAP 도구로 분리 (Phase 2+)

## 7. 데이터 활용처 (어디서 쓰이나)

| 데이터 | 활용 |
|---|---|
| recommendation_impressions | 추천 CTR 측정, CF 학습 데이터, 캐시 무효화 트리거 |
| book_co_occurrence | CF 알고리즘 (Stage 1+), "함께 좋아한 책" 큐레이션 |
| search_logs | 검색 개선, 검색 → 추천 학습 |
| user_books_history | wishlist → bad 추적, 알고리즘 품질 측정 |
| user_state | Tier 분류, 활성 유저 카운트, Stage 트리거 |

## 8. 프라이버시

- 모든 데이터는 user_id 기반 (개인 식별 가능)
- IP/디바이스 ID 미수집
- 유저 삭제 요청 시 cascade delete (FK constraint)

## 9. 구현 우선순위

### 즉시 (Phase 1A)
1. user_books 테이블 status 컬럼 추가 (마이그레이션)
2. user_books_history 테이블 + trigger
3. recommendation_impressions 테이블 + 앱 로깅
4. user_state 테이블 + 갱신 cron

### 1차 갱신 (Phase 1B)
5. book_co_occurrence 테이블 + 집계 워커
6. search_logs 테이블 + 검색 API 통합
7. 모니터링 대시보드 (내부용)

## 10. 출시 후 결정 항목

| 항목 | 비고 |
|---|---|
| Partition 도입 시점 | impression row 수 모니터링 |
| Read replica 도입 시점 | 분석 쿼리 부하 보고 |
| Redis 도입 여부 | 캐시 부하 보고 |

## 11. 범위 외

- 추천 알고리즘 자체 → 별도 spec
- 큐레이션 시스템 → 별도 spec
- 분석 dashboard UI → Phase 2+
- 데이터 export/BI 도구 → Phase 2+
