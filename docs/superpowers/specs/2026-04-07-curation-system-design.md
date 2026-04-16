# 큐레이션 시스템 설계 (자동 생성 + 개인화)

> 2026-04-07 | Eden
> 관련 spec: `2026-04-07-recommendation-algorithm-design.md`, `2026-04-07-data-collection-design.md`

## 1. 배경

추천 알고리즘 spec에서 정의한 Tier 시스템 중, **Tier 0 (좋아요 < 3권) 유저는 알고리즘 추천을 받지 않음**. 대신 큐레이션만 보여줌. 또한 Tier 1/2 유저에게도 추천 외에 큐레이션 섹션이 있음 (탐색, 작가 컬렉션 등).

**큐레이션 = 책의 묶음.** 책을 어떻게 묶어 보여줄지 결정하는 시스템.

핵심 요구사항 (Eden):
1. **다양한 주제 자동 생성** — 수동 큐레이션 X
2. **유저별 개인화** — 누구나 같은 화면 X
3. **확장성** — 새 주제/책이 자동으로 추가
4. **로딩 빠름** — 유저 경험 우선
5. **신간 가중치 매우 낮음** — 신간에 관심 있는 유저 적음
6. **수동 활성화 X** — 모든 자동

## 2. 두 layer 구조

알고리즘 spec과 동일한 분리:

| Layer | 내용 |
|---|---|
| Layer 1 — 확정 | 큐레이션 생성 메커니즘, 노출 알고리즘, 데이터 모델 |
| Layer 2 — 변수 | 큐레이션 수, 갱신 주기, LLM 사용량, 노출 가중치 |

## 3. 큐레이션 종류 (Layer 1)

### 3.1 자동 생성되는 4가지 type

| Type | 생성 방식 | 예시 |
|---|---|---|
| `genre_combo` | L1+L2 조합 | "한국 SF 소설", "심리학 에세이" |
| `author` | 저자 다작 | "무라카미 하루키 컬렉션" |
| `keyword` | library_keywords (정보나루) 클러스터 | "자아 찾기", "여성 서사" |
| `cluster` | 책 reason 임베딩 클러스터링 | "일상의 결을 붙잡는 글" |

**모두 rule-based + auto-generated**. 수동 list 없음.

### 3.2 개인화 기반 (Layer 1)

각 큐레이션에 노출 대상 정의:

```
personalization (테이블 컬럼)
├── "general"     - 모든 유저
├── "tier1+"      - Tier 1 이상
├── "tier2+"      - Tier 2 이상
├── "by_l1"       - 특정 L1 좋아한 유저
├── "by_author"   - 특정 저자 좋아한 유저
└── "by_keyword"  - 특정 키워드 매칭 유저
```

같은 큐레이션이 일부 유저에게만 노출되도록 필터링.

### 3.3 신간 (Eden 요청)

- `genre_combo` 안에 "최근 N일 이내" 필터를 가진 형태로 1개 정도만
- 큐레이션 풀 노출 가중치에서 **매우 낮음** (5% 이하)
- 별도 type으로 분리 안 함

## 4. 데이터 모델

### 4.1 curation_themes 테이블

```sql
CREATE TABLE curation_themes (
  id BIGSERIAL PRIMARY KEY,
  theme_type TEXT NOT NULL,             -- 'genre_combo' / 'author' / 'keyword' / 'cluster'
  title TEXT NOT NULL,                  -- 자동 생성
  description TEXT,                     -- 자동 생성
  selection_query JSONB NOT NULL,       -- 책 선택 룰 (SQL or vector query)
  parameters JSONB,                     -- 파라미터 (장르명, 저자, 키워드 등)
  min_books INT DEFAULT 5,              -- 최소 책 수 (못 채우면 비활성)
  max_books INT DEFAULT 30,             -- 노출 시 최대 책 수
  priority FLOAT DEFAULT 1.0,           -- 노출 가중치 (랜덤 sampling 시)
  personalization TEXT DEFAULT 'general',
  target_l1 TEXT,                       -- by_l1 일 때 L1 텍스트
  target_author TEXT,                   -- by_author 일 때
  target_keyword TEXT,                  -- by_keyword 일 때
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_shown_at TIMESTAMPTZ,
  shown_count INT DEFAULT 0,
  click_count INT DEFAULT 0,
  click_rate FLOAT GENERATED ALWAYS AS (
    CASE WHEN shown_count > 0 THEN click_count::float / shown_count ELSE 0 END
  ) STORED
);

CREATE INDEX idx_curation_active ON curation_themes (is_active, theme_type);
CREATE INDEX idx_curation_personalization ON curation_themes (personalization);
```

### 4.2 curation_cache 테이블

큐레이션 결과 미리 계산 (로딩 빠르게).

```sql
CREATE TABLE curation_cache (
  curation_id BIGINT PRIMARY KEY REFERENCES curation_themes(id),
  book_ids JSONB NOT NULL,              -- 현재 유효 book_ids 리스트
  cached_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);
```

**갱신**: 매 시간 백그라운드 워커가 selection_query 실행 → cache 갱신.

### 4.3 user_curation_history (개인화 + 다양성)

같은 유저에게 같은 큐레이션 반복 노출 방지.

```sql
CREATE TABLE user_curation_history (
  user_id UUID NOT NULL,
  curation_id BIGINT NOT NULL,
  shown_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (user_id, curation_id, shown_at)
);

CREATE INDEX idx_uch_user_recent ON user_curation_history (user_id, shown_at DESC);
```

## 5. 큐레이션 자동 생성 (Layer 1 메커니즘)

### 5.1 생성 워커 (백그라운드 cron)

매주 1회 자동 생성:

```
1. genre_combo 갱신
   - books 테이블에서 활성 L1×L2 조합 추출
   - 각 조합에 책 ≥ MIN_BOOKS 면 curation_themes에 row 추가/갱신

2. author 갱신
   - books에서 작가별 book count
   - count ≥ MIN_AUTHOR_BOOKS 작가에게 author 큐레이션 생성

3. keyword 갱신
   - library_keywords 빈도 분석
   - 상위 N개 키워드에 대해 큐레이션 생성

4. cluster 갱신 (월 1회)
   - book_v3_vectors의 desc_embedding 클러스터링 (KMeans)
   - 각 cluster에 대해 LLM으로 title/description 생성
```

### 5.2 LLM 활용 (제한적, Layer 1)

LLM은 **title/description 생성에만** 사용. 책 선택 자체는 rule-based.

```
Input: cluster 책 5개의 title + reason 샘플
Output: {
  "title": "일상의 결을 붙잡는 한국 에세이",
  "description": "차분히 걷듯 읽게 되는 글들"
}
```

**비용**:
- 매주 ~50~100개 큐레이션 갱신/생성
- gpt-4o-mini, ~$0.01/큐레이션
- **주당 ~$1, 월 ~$4**. 극소.

**LLM 결과 검증**:
- title 길이 5~30자 체크
- 금지 단어 필터 (예: "최고", "1위" 등 광고성)
- 검증 실패 시 fallback (template 기반 title)

### 5.3 비활성화 자동 처리

매일 cron:
- `min_books` 못 채우는 큐레이션 → `is_active = false`
- 30일간 노출 0회 + click 0회 → 비활성
- click_rate < 0.5% (90일 누적) → 비활성

## 6. 노출 알고리즘 (Layer 1)

### 6.1 큐레이션 풀 만들기

유저가 홈 진입 시:

```python
def select_curations_for_user(user_id, n_sections=4):
    user_state = get_user_state(user_id)
    tier = user_state.current_tier

    # 1. 후보 풀: 유저 tier에 맞는 활성 큐레이션
    pool = curation_themes WHERE
        is_active = TRUE
        AND personalization IN allowed_personalization_for(tier, user_state)
        AND id NOT IN recently_shown_to(user_id, last_7_days)

    # 2. 가중치 계산
    for c in pool:
        c.weight = c.priority
        if c.theme_type == 'genre_combo' and '신간' in c.parameters:
            c.weight *= 0.05  # 신간 매우 낮춤 (Eden)
        if c.click_rate > 0.05:
            c.weight *= 1.5  # 잘 작동하는 큐레이션 우선
        if c.personalization in ('by_l1', 'by_author', 'by_keyword'):
            c.weight *= 2.0  # 개인화 우선

    # 3. 가중 랜덤 sampling N개
    selected = weighted_sample(pool, n_sections)

    # 4. 각 selection 의 책은 cache에서 읽기
    return [(c, get_cache(c.id)) for c in selected]
```

### 6.2 Tier별 섹션 구성

**Tier 0 (0~2권)** — 4 섹션:
1. 화제의 책 (fallback_curation — Strategy C: 정보나루 loan_count_12mo top 20 + 알라딘 sales_point top 10 보완, 제목 dedup, 항상 노출. 상세: `2026-04-16-data4library-aladin-hybrid-collection.md`)
2. 동적 큐레이션 1 (general 중 가중 랜덤)
3. 동적 큐레이션 2 (다른 type)
4. 카테고리 탐색 (장르 트리, 정적)

**Tier 1 (3~5권)** — 5 섹션:
1. "○○과 비슷한 책" (similar 엔진 — 알고리즘 spec)
2. "○○ 작가의 다른 책" (by_author 큐레이션)
3. by_l1 큐레이션 (유저 좋아한 L1 기반)
4. 동적 큐레이션 (탐색용)
5. 카테고리 탐색

**Tier 2 (6권+)** — 5 섹션:
1. "당신을 위한 추천" (H10/Hybrid — 알고리즘 spec)
2. "○○ 작가의 다른 책" (by_author)
3. "○○과 비슷한 책" (similar)
4. 새 영역 탐색 (by_l1/keyword에서 유저 안 본 것)
5. 화제의 책

### 6.3 노출 다양성 (Layer 1)

같은 유저가 같은 화면에 매번 똑같이 보이지 않도록:

1. **최근 7일 노출 디스카운트** — `user_curation_history` 조회
2. **가중 랜덤 sampling** — 같은 priority여도 매번 다른 큐레이션
3. **세션 동안 고정** — 한 세션 내에서는 안 변함 (추천 일관성, 알고리즘 spec)
4. **새로고침/재진입 시 갱신** — 새 큐레이션 노출 가능

## 7. 로딩 성능 (Layer 1)

### 7.1 캐시 계층

```
[L1] curation_cache 테이블
  - 각 큐레이션의 현재 유효 book_ids
  - 매 시간 백그라운드 갱신
  - 유저 진입 시 read only

[L2] user_state 테이블
  - tier, 좋아한 L1/저자 정보
  - 유저 액션 시 trigger로 갱신
```

### 7.2 유저 진입 시 흐름

```
GET /home (Tier 0 예시):
1. user_state 조회 (1ms, single row)
2. select_curations_for_user 실행:
   - curation_themes WHERE filter (인덱스, 5ms)
   - 가중 랜덤 sampling (in-memory, < 1ms)
3. 각 selected curation의 cache 조회 (4 × 2ms = 8ms)
4. 응답 조립 → return

총: < 30ms
```

### 7.3 비동기 처리

- **유저 좋아요/저장 → 즉시 응답**
- 백그라운드: user_state 갱신, recommendation_impressions 기록
- 다음 새로고침/재진입 시 갱신된 데이터로 큐레이션 재선택

## 8. UI 표시

각 Tier별 화면 구성은 위 6.2 참조.

### 큐레이션 카드 디자인 (UI 가이드)

```
┌─────────────────────────────┐
│ 📖 [큐레이션 title]          │
│ [큐레이션 description]      │
│                              │
│ [책 카드 1] [책 카드 2] ...  │
│ ← 가로 스크롤 →             │
└─────────────────────────────┘
```

### CTA (Tier 진행 안내)

**Tier 0**: "좋아요 N권 더 누르면 비슷한 책 추천이 시작돼요"
**Tier 1**: "좋아요 N권 더 평가하면 취향 추천이 시작돼요"
**Tier 2**: 없음

(상세 UI는 product spec)

## 9. 모니터링 (Layer 1)

매일 자동 측정:

| 지표 | 목적 |
|---|---|
| 큐레이션 type별 분포 | genre/author/keyword/cluster 비율 |
| 큐레이션 평균 click rate | 어떤 type이 효과적인지 |
| Tier 0 → Tier 1 전환율 | 큐레이션이 평가 유도하는지 |
| 큐레이션 신선도 | 평균 노출 횟수, 30일 내 미노출 비율 |
| LLM 호출 횟수/비용 | 비용 모니터링 |

## 10. 출시 후 결정 항목 (Layer 2)

| 항목 | 초기 추정 | 조정 시점 |
|---|---|---|
| 매주 자동 생성 큐레이션 수 | 50~100개 | 출시 30일 후 |
| 큐레이션 cache TTL | 1시간 | 부하 측정 후 |
| MIN_BOOKS | 5 | 출시 후 |
| MIN_AUTHOR_BOOKS | 3 | 출시 후 |
| 신간 노출 가중치 | 0.05 | 클릭 데이터 보고 |
| 개인화 가중치 (by_*) | 2.0 | A/B 없이 단계적 조정 |
| 클러스터링 cluster 수 | 30~50 | 책 수 증가에 따라 |

## 11. 확장성

### 새 큐레이션 type 추가
- `theme_type` 컬럼에 새 값 추가
- 생성 워커에 새 type 핸들러 추가
- 기존 시스템 영향 없음

### 새 personalization 추가
- `personalization` 컬럼에 새 값 추가
- `select_curations_for_user`의 필터 로직에 추가
- 기존 시스템 영향 없음

### 책 풀 증가
- selection_query는 매번 실행되므로 자동 반영
- cache 갱신만 cron으로 처리

### LLM 모델 변경
- title/description 생성기만 교체
- spec 변경 없음

## 12. 범위 외

- **추천 알고리즘** → 별도 spec
- **데이터 수집** → 별도 spec
- **wishlist UI** → product spec
- **검색 기능 자체** → 별도 spec
- **사회적 큐레이션 (친구가 좋아한 책)** → Phase 2+
- **실시간 트렌드 큐레이션** → Phase 2+

## 13. 핵심 원칙

1. **수동 작업 금지** — 모든 것 자동 생성/갱신/비활성화
2. **확장성 최우선** — 새 type/책/유저 자동 수용
3. **로딩 < 30ms** — cache 활용
4. **개인화 + 다양성 균형** — 같은 유저도 매번 다른 발견
5. **LLM은 제한적으로** — 비용 < 월 $5
6. **신간 가중치 매우 낮음** — Eden 명확한 의견
