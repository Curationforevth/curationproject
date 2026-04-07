# 추천 엔진 Cold/Warm 두 축 통합 설계 (DEPRECATED)

> ⚠️ **이 spec은 3개로 분할되었습니다.** 통합 spec이 너무 크고 검증 안 된 가정이 많아 실행 가능한 단위로 분리:
> - `2026-04-07-recommendation-algorithm-design.md` (추천 알고리즘)
> - `2026-04-07-data-collection-design.md` (데이터 수집 인프라)
> - `2026-04-07-curation-system-design.md` (큐레이션 시스템)
>
> 이 spec은 참고용으로 보관.

> 2026-04-07 | Eden

## 1. 배경

현재 v3 추천 엔진은 100% content-based(desc + reason + L1/L2 + fb_desc)이며, 18 페르소나 + 50 랜덤 + 4모드 검증을 통해 다음이 확인됨:

1. **L1/L2 임베딩은 binary처럼 작동**(cosine 1.0/0.3 양극화) → 가중치 의미 없음
2. **desc는 정상 분포**이며 가장 변별력 있는 신호
3. **취향 매칭의 본질적 한계**: content-based만으로는 "담담한 톤", "정제된 사유" 같은 추상 취향을 잡기 어려움. Reason 재생성으로 풀리지 않음
4. Netflix/Spotify/Amazon 등 대규모 추천 서비스는 모두 **Collaborative Filtering(CF)을 핵심으로 사용**하며, content는 cold start 보조 역할

근본 해결책은 유저 행동 데이터 누적 후 CF 도입이지만, 지금은 유저가 0이라 cold start만 가능. **두 축을 동시에 설계하여, cold 단계 운영 중에 warm 단계 진입을 위한 데이터가 자동으로 쌓이도록** 한다.

## 2. 두 축 정의

### Cold (Phase 1) — 출시부터 활성 유저 250명까지

- **알고리즘**: H10_no_l1 (content-based)
  - 가중치: `reason 2.0 + desc 3.0 + l1 0.0 + l2 0.0 + fb_desc 2.0`
  - 후처리: `cap_dynamic` (유저가 읽은 L1 분포 비례)
- **포지셔닝**: "당신이 좋아한 책과 비슷한 책"
- **검증된 결과**: 18 페르소나 + 50 랜덤 + 적대적 입력 모두 통과 (Test A: 88%/50 케이스에서 베이스라인 우수, 안정성 평균 0.92 overlap)

### Warm (Phase 2) — 활성 유저 250명+

- **알고리즘**: Hybrid (Content + CF)
- **포지셔닝**: "당신만을 위한 추천"
- **CF 종류**: Item-to-Item Collaborative Filtering (Amazon 스타일) — 계산 단순, 작은 도서 풀(2,651권)에 적합

### 전환 트리거

- **조건**: 총 유저 500명 + 활성 유저 50% 이상 (= 활성 유저 250명)
  - "활성"의 정의: 최근 30일 내 좋아요/저장/리뷰 1건 이상
- **전환 방식**: 자동 트리거 + 수동 확인 (PM이 트리거 발동 후 결과 검증 → 배포)
- **롤백**: Hybrid 결과가 cold 대비 metric 하락 시 즉시 cold로 롤백 가능

## 3. 데이터 수집 (Phase 1부터 즉시 시작)

### 3.1 신규 수집 항목 8가지

| # | 데이터 | 정의 | 저장 위치 | 우선순위 |
|---|---|---|---|---|
| 1 | impression_log | 추천 노출 기록 (user_id, book_id, position, recommended_at, source) | `recommendation_impressions` 테이블 | P0 |
| 2 | impression_action | 노출 후 행동 (clicked / saved / liked / disliked / ignored) | 1번 테이블의 `action` 컬럼 | P0 |
| 3 | re_recommendation_count | 같은 user×book이 노출된 횟수 | 1번 테이블 집계 | P0 |
| 4 | wishlist_to_bad | 저장(읽고싶음)했다가 bad로 평가한 케이스 | `user_books` 상태 변화 추적 | P0 |
| 5 | wishlist 분리 | 저장(읽고싶음)과 좋아요(평가) 명확히 분리 | `user_books.status` 컬럼 정리 | P0 |
| 6 | search_query | 유저 검색 쿼리 + 검색 결과 클릭 여부 | `search_logs` 테이블 | P1 |
| 7 | session_pattern | 추천 발생 → 유저 행동까지의 시간 | impression_log의 `clicked_at - shown_at` | P1 |
| 8 | consecutive_ignore | 추천 N권 연속 무시 카운터 | `user_state.consecutive_ignores` | P1 |
| 9 | co_likes | (book_a, book_b) 함께 좋아요 받은 횟수 | `book_co_occurrence` 테이블 | P0 (자동 집계) |

### 3.2 user_books 상태 머신 정리

현재: `rating` 컬럼만 (good/neutral/bad)

신규 설계:

```
status: wishlist → reading → finished
rating: null → good/bad (finished 전에도 변경 가능)
```

| status | 의미 |
|---|---|
| wishlist | 읽고 싶음 (저장만) |
| reading | 읽는 중 |
| finished | 다 읽음 |

`wishlist_to_bad` 추적 = `status=wishlist` 였다가 `rating=bad` 된 이력 = `user_books_history` 로그 테이블 필요

### 3.3 신규 테이블 스키마

```sql
-- 추천 노출 로그
CREATE TABLE recommendation_impressions (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL REFERENCES books(id),
  position INT,                    -- Top N에서의 순위
  source TEXT,                     -- 'home_recommend' / 'similar' / 'search'
  algorithm_version TEXT,          -- 'v3-cold' / 'v3-warm' 등
  shown_at TIMESTAMPTZ DEFAULT NOW(),
  action TEXT,                     -- 'clicked' / 'saved' / 'liked' / 'disliked' / null(ignored)
  action_at TIMESTAMPTZ,
  INDEX (user_id, shown_at DESC),
  INDEX (book_id)
);

-- 책 간 공동 발생 (co-occurrence) — 백그라운드 집계
CREATE TABLE book_co_occurrence (
  book_a_id UUID NOT NULL REFERENCES books(id),
  book_b_id UUID NOT NULL REFERENCES books(id),
  co_like_count INT DEFAULT 0,     -- 함께 좋아요 받은 횟수
  co_save_count INT DEFAULT 0,     -- 함께 저장된 횟수
  updated_at TIMESTAMPTZ,
  PRIMARY KEY (book_a_id, book_b_id),
  CHECK (book_a_id < book_b_id)    -- 양방향 중복 방지
);

-- 검색 로그
CREATE TABLE search_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID,
  query TEXT NOT NULL,
  result_count INT,
  clicked_book_id UUID REFERENCES books(id),
  searched_at TIMESTAMPTZ DEFAULT NOW(),
  INDEX (user_id, searched_at DESC)
);

-- user_books 변경 이력 (wishlist → bad 추적용)
CREATE TABLE user_books_history (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL REFERENCES books(id),
  old_status TEXT,
  new_status TEXT,
  old_rating TEXT,
  new_rating TEXT,
  changed_at TIMESTAMPTZ DEFAULT NOW()
);

-- 유저 상태 (집계 캐시)
CREATE TABLE user_state (
  user_id UUID PRIMARY KEY,
  consecutive_ignores INT DEFAULT 0,    -- 연속 무시 카운터
  last_active_at TIMESTAMPTZ,
  total_likes INT DEFAULT 0,
  total_saves INT DEFAULT 0,
  is_active BOOLEAN DEFAULT FALSE       -- 30일 내 활동 여부
);
```

### 3.4 백그라운드 집계 작업

매일 또는 매시간 실행:

| 작업 | 주기 | 내용 |
|---|---|---|
| co_occurrence 갱신 | 매일 새벽 | 신규 like/save에서 페어 추출 → count 증가 |
| user_state 갱신 | 매시간 | last_active_at, is_active, consecutive_ignores |
| 활성 유저 카운트 | 매일 | 전환 트리거 모니터링용 (총/활성 유저 수) |

## 4. 알고리즘 통합 (점진적)

### 4.1 점수 공식 — 단계별

**Phase 1 (Cold, 데이터 부족):**
```
score = 3.0 × desc + 2.0 × reason + 2.0 × fb_desc
```

**Phase 1.5 (데이터 쌓이는 중, 자동 활성화):**
```
score = 3.0 × desc + 2.0 × reason + 2.0 × fb_desc
      + α × co_occurrence_boost          # data sufficiency에 따라 0~2.0
      + β × consecutive_ignore_penalty   # 0 또는 음수
```

`α`, `β`는 데이터 누적량에 따라 자동 증가:
- `α` (co_occurrence boost): book_co_occurrence 페어 수에 따라 단계적
  - < 1,000 페어: α = 0 (무시)
  - 1,000 ~ 10,000: α = 0.5
  - 10,000 ~ 50,000: α = 1.0
  - 50,000+: α = 2.0 (Phase 2 진입 임박)
- `β` (consecutive_ignore penalty): user_state.consecutive_ignores 기준
  - < 3: β = 0
  - 3 ~ 4: β = -0.5 (top 추천 점수 약간 감점)
  - 5+: reset 모드 발동 (4.2 참조)

**Phase 2 (Warm, 250 활성 유저+):**
```
score = w_content × content_score + w_cf × cf_score

content_score = (Phase 1.5 공식)
cf_score = item_to_item CF score (book_co_occurrence + user 행동 기반)
w_content, w_cf = 동적 (유저별 데이터 양에 따라)
```

신규 유저: `w_cf = 0` (cold)
일반 유저: `w_content = 0.4, w_cf = 0.6`
다독가: `w_content = 0.2, w_cf = 0.8`

### 4.2 Reset 메커니즘 (연속 추천 거부 대응)

`user_state.consecutive_ignores >= 5` 이면:
1. 다음 추천은 **diversity 강제** (cap_dynamic 비율 깨고 새 L1 강제 포함)
2. 또는 **explore mode**: top-N 대신 top-50에서 무작위 sampling
3. 유저가 1건이라도 클릭하면 카운터 reset

## 5. UI / 제품 변경 사항

### 5.1 wishlist 분리 (P0)
- 현재: 좋아요 = 평가 + 저장 혼재
- 개선: 명확히 분리
  - 책 카드에 두 버튼: **"읽고 싶음" (저장)** vs **"평가하기" (좋아요/싫어요)**
  - 서재 탭에서 wishlist / reading / finished 3개 필터

### 5.2 추천 노출 추적 (P0)
- 홈 화면 추천 섹션이 노출되면 → impression 자동 기록
- 책 상세 진입 → action='clicked' 기록
- 좋아요/저장/싫어요 → action 업데이트

### 5.3 검색 추적 (P1)
- 검색 시 query + result_count 기록
- 검색 결과에서 책 클릭 시 clicked_book_id 기록

### 5.4 추천 explanation (선택)
- "비슷한 책" 표시 (cold)
- "○○님과 비슷한 취향의 사람들이 좋아한 책" 표시 (warm)

## 6. 모니터링 / 전환 트리거

### 6.1 핵심 지표

| 지표 | 측정 주기 | 알람 임계 |
|---|---|---|
| 총 유저 수 | 매일 | 500 도달 시 알림 |
| 활성 유저 수 (30일) | 매일 | 250 도달 시 알림 |
| 책당 평균 평점 수 | 매주 | 10 이상 권장 |
| co_occurrence 페어 수 | 매주 | 10,000+ 권장 |
| 추천 클릭률 (CTR) | 매일 | 기준선 대비 ±20% |
| 연속 무시 평균 | 매주 | 3 이하 유지 |

### 6.2 전환 의사결정

500명 + 활성 250명 도달 시:
1. 자동 알림
2. PM이 데이터 검증 (책당 평점 밀도, co_occurrence 충분성 등)
3. Hybrid 알고리즘 A/B 테스트 (10% 유저 대상)
4. 7일 후 metric 비교 → 채택 / 롤백

## 7. 구현 우선순위

### Phase 1A — 즉시 (Cold 출시)

1. **H10_no_l1 적용** — config.py 가중치 변경 + cap_dynamic 후처리 추가
2. **wishlist / 좋아요 UI 분리** — Flutter 앱 변경
3. **impression_log 수집 시작** — 백엔드 + 앱 양쪽
4. **user_books_history 트리거** — Postgres trigger로 자동
5. **monitoring 대시보드** — 유저/활성/지표 추적

### Phase 1B — 데이터 쌓이는 중 (1~3개월)

6. **co_occurrence 백그라운드 집계** — 매일
7. **search_logs 수집** — 검색 기능 추가/개선
8. **consecutive_ignore reset** — 알고리즘 보정
9. **co_occurrence boost (α)** — 단계적 활성

### Phase 2 — Warm 진입 시 (활성 250명+)

10. **Item-to-Item CF 알고리즘** 구현
11. **Hybrid 점수 공식** 적용
12. **A/B 테스트**
13. **롤백 메커니즘**

## 8. 범위 외 (이번 spec 제외)

- 유저 프로필 기반 demographics (나이/성별 등) — Privacy 부담, 효과 불확실
- Deep learning 모델 (Two-tower 등) — 우리 규모엔 과함
- 시간대/세션 시간 — Eden 의견에 따라 제외
- 공유/스크린샷 추적 — Eden 의견에 따라 제외
- 읽기 후 평가 변화 — Eden 의견에 따라 제외 (대신 wishlist→bad로 대체)
- 체류 시간 — Eden 의견에 따라 제외

## 9. 핵심 원칙

1. **Cold에서 출발하되 Warm을 위한 데이터를 첫날부터 수집한다**
2. **알고리즘 변경은 점진적이고 가역적이어야 한다** (롤백 항상 가능)
3. **유저 행동 데이터는 가설이 아닌 실측이 우선** — 가중치는 데이터 보고 결정
4. **Cold start 케이스는 사라지지 않는다** — 신규 유저는 항상 발생, content 경로는 영구 유지
