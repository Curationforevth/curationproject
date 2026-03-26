# 추천 엔진 v2 설계 스펙 — "좋아할 이유" 기반 매칭

> **상태:** 설계 완료, 구현 대기
> **이전 버전:** `2026-03-26-recommendation-engine-design.md` (속성 점수 기반 → 폐기)

---

## 핵심 전환

| | v1 (폐기) | v2 (이 문서) |
|--|----------|-------------|
| **책 표현** | 15개 고정 속성 점수 (0~1) | "좋아할 이유" 자유 텍스트 리스트 |
| **유저 취향** | 임베딩 가중 평균 | "좋아하는 이유" 텍스트 리스트 |
| **후보 생성** | 임베딩 유사도 (primary) | 이유 임베딩 매칭 (primary) |
| **재정렬** | 속성 점수 재정렬 (secondary) | 임베딩 유사도 (secondary, sanity check) |

**왜 바꾸는가:**
- 고정 속성 15개는 책의 다양한 매력을 담을 수 없음 (감성태그 10개 한정과 동일한 문제)
- 임베딩 유사도 기반 후보 생성은 피드백 내용과 무관한 결과를 냄
- v2는 피드백이 직접 후보 풀을 결정하므로, 같은 책에 다른 피드백 → 다른 추천

---

## 원칙 (변경 없음)

1. **"왜 좋아했는가"가 취향을 결정한다**
2. **피드백이 다르면 추천이 다르다** — 차이에 비례해서
3. **확장적** — 유저당 비용 증가가 아닌, 책당 1회 처리
4. **유저가 쓸수록 더 좋아진다** — 피드백이 책 프로필을 보강

---

## 데이터 모델

### 1. 책의 "좋아할 이유" (book_love_reasons)

```sql
CREATE TABLE book_love_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id UUID REFERENCES books(id) NOT NULL,
  reason TEXT NOT NULL,              -- "호그와트의 수업, 기숙사, 퀴디치 등 디테일하게 구축된 마법 학교 생활"
  reason_embedding VECTOR(3072),     -- text-embedding-3-large
  source TEXT NOT NULL,              -- 'llm_extracted' | 'user_feedback'
  user_mention_count INT DEFAULT 0,  -- user_feedback인 경우, 몇 명이 비슷한 말을 했는지
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_blr_book ON book_love_reasons(book_id);
CREATE INDEX idx_blr_embedding ON book_love_reasons USING ivfflat (reason_embedding vector_cosine_ops);
```

**생성 규칙:**
- 초기: 책 등록 시 LLM이 5~8개 추출 (source='llm_extracted')
- 보강: 유저 피드백에서 기존 이유와 유사도 < 0.7인 새로운 이유가 2명 이상 언급되면 추가 (source='user_feedback')

**이유 작성 형식:**
- 한 문장, 10~30단어
- 구체적 요소를 포함 (장면, 캐릭터, 설정 등)
- "이 책의~" 서두 없이 핵심만
- 판본/에디션이 아닌 작품 자체에 대한 내용

**예시 (해리 포터와 마법사의 돌):**
```
- 호그와트의 수업, 기숙사, 퀴디치 등 디테일하게 구축된 마법 학교 생활
- 해리, 론, 헤르미온느의 성장하는 우정과 팀워크
- 마법사의 돌을 둘러싼 미스터리와 반전
- 빠르게 읽히는 모험 중심 전개
- 선과 악의 대립 속에서 용기와 사랑의 메시지
- 다양한 마법 생물과 주문 등 상상력 넘치는 디테일
```

### 2. 유저의 "좋아하는 이유" (user_taste_reasons)

```sql
CREATE TABLE user_taste_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) NOT NULL,
  book_id UUID REFERENCES books(id) NOT NULL,  -- 어떤 책에 대한 피드백에서 나온 건지
  reason TEXT NOT NULL,                         -- "새롭고 디테일한 세계관"
  reason_embedding VECTOR(3072),
  weight FLOAT NOT NULL DEFAULT 1.0,            -- rating 기반: good=1.0, neutral=0.5, bad 제외
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_utr_user ON user_taste_reasons(user_id);
CREATE INDEX idx_utr_embedding ON user_taste_reasons USING ivfflat (reason_embedding vector_cosine_ops);
```

**생성 시점:** 유저가 피드백 제출 시 즉시
- 피드백 텍스트 → LLM으로 이유 추출 (2~6단어 짧은 구)
- 각 이유를 text-embedding-3-large로 임베딩
- weight = rating에 따라 (good=1.0, neutral=0.5, bad=0.0)
- bad 피드백의 이유는 저장하되 weight=0 (negative signal로 향후 활용 가능)

---

## 추천 파이프라인

### MVP 추천 1: 서재 메인 → "추천 섹션" (taste-to-book)

```
유저의 taste_reasons (N개)
  ↓
각 reason_embedding으로 book_love_reasons 검색
  → 각 이유당 top-K 매칭 (cosine similarity on reason_embedding)
  ↓
후보 책 합산 점수 계산
  score(book) = Σ (user_reason_weight × max_similarity(user_reason, book_reasons))
  ↓
이미 서재에 있는 책 제외
중복 판본 제외 (canonical_book_id)
  ↓
Top-N 추천 결과
```

**RPC 구현:**

```sql
-- 유저의 이유 임베딩들과 가장 매칭되는 책 찾기
CREATE OR REPLACE FUNCTION recommend_books_by_reasons(
  p_user_id UUID,
  p_match_count INT DEFAULT 20
)
RETURNS TABLE (book_id UUID, title TEXT, score FLOAT, matched_reason TEXT)
AS $$
  WITH user_reasons AS (
    SELECT reason_embedding, weight
    FROM user_taste_reasons
    WHERE user_id = p_user_id AND weight > 0
  ),
  -- 각 유저 이유에 대해 가장 매칭되는 책 이유 찾기
  reason_matches AS (
    SELECT
      blr.book_id,
      ur.weight * (1 - (blr.reason_embedding <=> ur.reason_embedding)) AS match_score,
      blr.reason AS matched_reason
    FROM user_reasons ur
    CROSS JOIN LATERAL (
      SELECT book_id, reason_embedding, reason
      FROM book_love_reasons
      ORDER BY reason_embedding <=> ur.reason_embedding
      LIMIT 100
    ) blr
  ),
  -- 책별 합산
  book_scores AS (
    SELECT
      rm.book_id,
      SUM(rm.match_score) AS total_score,
      (ARRAY_AGG(rm.matched_reason ORDER BY rm.match_score DESC))[1] AS top_reason
    FROM reason_matches rm
    WHERE rm.book_id NOT IN (
      SELECT fb.book_id FROM user_book_feedback fb WHERE fb.user_id = p_user_id
    )
    AND rm.book_id NOT IN (
      SELECT b.id FROM books b WHERE b.canonical_book_id IS NOT NULL
    )
    GROUP BY rm.book_id
  )
  SELECT bs.book_id, b.title, bs.total_score AS score, bs.top_reason AS matched_reason
  FROM book_scores bs
  JOIN books b ON b.id = bs.book_id
  ORDER BY bs.total_score DESC
  LIMIT p_match_count;
$$ LANGUAGE sql;
```

### MVP 추천 2: 책 상세 → "비슷한 책" (book-to-book)

기존 `match_books_by_similarity` RPC 유지. 이건 임베딩 유사도로 충분.
단, canonical_book_id가 있는 중복 판본은 결과에서 제외.

---

## 배치 파이프라인

### 기존 파이프라인 (유지)

| 파이프라인 | 시간 | 역할 |
|-----------|------|------|
| daily-collect | 03:00 KST | 알라딘 신간 수집 |
| daily-scrape | 2시간마다 | Yes24 보강 |
| daily-embed-t2 | 06:30 KST | Tier2 임베딩 |
| daily-taste-recompute | 07:00 KST | 취향 벡터 갱신 |

### 추가 파이프라인

| 파이프라인 | 시간 | 역할 |
|-----------|------|------|
| **daily-extract-reasons** | 05:00 KST | 새 책의 "좋아할 이유" 추출 + 임베딩 |
| **daily-enrich-reasons** | 07:30 KST | 유저 피드백 기반 이유 보강 (2명+ 기준) |

### daily-extract-reasons

```
1. book_love_reasons가 없는 책 조회
2. 각 책에 대해:
   a. LLM으로 "좋아할 이유" 5~8개 추출
      - 입력: title, genre, description, rich_description, library_keywords
      - 프롬프트: 작품 자체의 매력, 구체적 요소 포함, 10~30단어 문장
   b. text-embedding-3-large로 각 이유 임베딩
   c. book_love_reasons에 저장
```

**비용 추정 (8,500권 초기 배치):**
- LLM: gpt-4o-mini × 8,500 = ~$2 (입출력 합산)
- 임베딩: ~50,000 텍스트 × embedding-3-large = ~$0.5
- 총 초기 비용: ~$2.5
- 이후: 일 10~30권 신규 = 무시할 수준

### daily-enrich-reasons

```
1. 최근 24시간 내 유저 피드백의 taste_reasons 조회
2. 각 reason에 대해:
   a. 해당 책의 기존 book_love_reasons와 유사도 비교
   b. 모든 기존 이유와 유사도 < 0.7이면 "새로운 이유" 후보
   c. 같은 책에 대해 2명+ 유저가 비슷한 새 이유를 언급했는지 확인
   d. 조건 충족 시 book_love_reasons에 추가 (source='user_feedback')
```

---

## 즉시 경로 — 피드백 제출 시

```
유저 피드백 제출
  ↓
1. user_book_feedback에 저장 (기존)
2. LLM으로 "좋아하는 이유" 추출 (2~6단어 짧은 구, ~1초)
3. text-embedding-3-large로 임베딩 (~0.5초)
4. user_taste_reasons에 저장
5. (기존) recompute_taste_vector_immediate 호출 (하위 호환)
```

**유저 체감 시간:** ~1.5초 (LLM + 임베딩). 피드백 UI에서 "취향 분석 중..." 같은 피드백 제공.

---

## 임베딩 모델

**text-embedding-3-large (3072차원)**

실험 결과 text-embedding-3-small 대비:
- 정확도: 83% → 100% (6개 테스트 케이스)
- "재밌는 소재" ↔ "과거와 현재를 오가는 편지 매개 시간여행 소재": small에서는 매칭 실패, large에서 1위

**비용:**
- small: $0.02/1M tokens
- large: $0.13/1M tokens (6.5배)
- 이유 텍스트가 짧아서(10~30단어) 절대 비용은 낮음

**pgvector 저장:**
- vector(3072) 컬럼
- ivfflat 인덱스 (8,500권 × 7이유 = ~60,000 벡터 → ivfflat 충분)

---

## 추천 신뢰도

기존 confidence score 구조 유지하되, 판단 기준 변경:

| 조건 | 추천 가능 여부 |
|------|--------------|
| taste_reasons 0개 | 추천 불가 (피드백 없음) |
| taste_reasons 1~2개 | 제한적 추천 ("더 많은 피드백을 남기면 추천이 정확해져요") |
| taste_reasons 3개+ | 정상 추천 |

기존 taste_vector 기반 신뢰도는 book-to-book 추천에서 계속 활용.

---

## 기존 시스템과의 관계

| 기존 구성요소 | 처리 |
|-------------|------|
| taste_vector (가중 평균) | **유지** — book-to-book 추천, K-means 클러스터링에 사용 |
| taste_recomputer.py | **유지** — taste_vector 갱신 담당 |
| match_books_by_similarity RPC | **유지** — book-to-book에 사용 |
| recommend_books_for_user RPC | **대체** — reason 기반 RPC로 교체 |
| 감성태그 (mood_tags) | **유지** — 온보딩 입력 UI, taste_reasons와 별개 |
| 기존 임베딩 (embedding-3-small) | **유지** — book-to-book 유사도에 사용. reason 임베딩은 별도 |

---

## 실험 결과 요약 (2026-03-26)

### "좋아할 이유" 추출 품질
- LLM 자동 추출 시 프롬프트가 핵심. "작품 자체의 매력, 구체적 요소 포함" 지시 필요.
- 마케팅 카피 기반 추출은 실패 (해리포터 → 미나리마 에디션 이유만 추출됨)
- LLM의 작품 지식 활용 시 품질 좋음

### 매칭 방법 비교

| 방법 | 정확도 (6건) | 비고 |
|------|------------|------|
| embedding-3-small | 83% | "재밌는 소재" 매칭 실패 |
| **embedding-3-large** | **100%** | 모든 케이스 정확 |
| 피드백 확장 + small | 83% | 확장이 노이즈 추가 |
| LLM judge | 83% | "재밌는 소재"에서 오판, 비확장적 |

### 핵심 검증: 같은 책, 다른 피드백 → 다른 추천

해리포터에 대해:
- A: "세계관이 디테일" → 반지의제왕, 1984가 상위
- B: "캐릭터 우정 감동" → 해리포터(자체 매칭), 반지의제왕, 나미야잡화점이 상위
- **순서가 바뀜 → 목적 달성 확인**

---

## 미구현 / 향후

- [ ] recency decay (시간 가중치)
- [ ] bad 피드백의 negative signal 활용
- [ ] 추천 이유 텍스트 ("이 책을 추천하는 이유: 당신이 좋아하는 '디테일한 세계관'이 이 책에서도...")
- [ ] K-means 클러스터별 추천 (다중 취향)
- [ ] is_onboarding_favorite 기반 초기 taste_reasons 생성
