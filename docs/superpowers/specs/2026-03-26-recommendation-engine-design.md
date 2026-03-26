# 추천 엔진 설계 스펙

> **상태:** 초안. 구조 확정, 파라미터는 실험으로 튜닝 필요.

---

## 목표

MVP에서 추천이 작동해야 한다. "취향 분석 & 추천 앱"인데 추천이 없으면 MVP가 아니다.

---

## MVP 추천 2가지

### 1. 책 상세 → "비슷한 책" (book-to-book)

- **작동 조건:** 없음. 책 1권만 있어도 작동
- **로직:** 해당 책의 임베딩 벡터로 pgvector 코사인 유사도 → Top-N 유사 책
- **용도:** 콜드스타트 해결. 취향 벡터가 없어도 추천 경험 제공
- **쿼리:** Supabase RPC → `book_embeddings` 테이블 HNSW 인덱스

### 2. 서재 메인 → "추천 섹션" (taste-to-book)

- **작동 조건:** 추천 신뢰도 스코어가 임계값 이상
- **로직:** 유저 취향 벡터로 pgvector 코사인 유사도 → 읽은 책 제외 → Top-N 추천
- **용도:** 핵심 추천 경험. "입력할수록 추천이 좋아진다"를 체감
- **쿼리:** Supabase RPC → `user_taste_vectors` × `book_embeddings`

---

## 아키텍처

### 두 경로 구조

| 경로 | 방식 | 용도 | 지연 |
|------|------|------|------|
| **즉시** | Supabase RPC / Edge Function | book-to-book 유사도, 개별 유저 취향 벡터 재계산, 신뢰도 스코어 | <100ms |
| **배치** | GitHub Actions Python 스크립트 | 전체 유저 취향 벡터 갱신, LLM 취향 요약, 새 임베딩 반영, 정교한 클러스터링 | 일/주 |

### 즉시 경로 — 유저가 피드백 남길 때

```
유저 피드백 제출 (rating + 감성태그 + 리뷰)
  → Supabase Edge Function 트리거
  → 유저의 모든 읽은 책 임베딩 조회
  → 가중 평균으로 취향 벡터 즉시 계산
  → user_taste_vectors에 upsert
  → 추천 신뢰도 스코어 갱신
```

유저가 피드백 남기고 서재로 돌아오면 **즉시** 추천이 반영된다.

### 배치 경로 — 정교한 분석

```
daily-embed-t2 완료 후 (또는 주 1회):
  → 전체 유저 순회
  → 충분한 데이터가 있는 유저: K-means 클러스터링 → 다축 취향 벡터
  → LLM 취향 요약 생성 ("당신은 잔잔한 캐릭터 성장 서사를 좋아하는 독자예요")
  → 추천 이유 생성 ("이 책의 서정적인 문체가 마음에 드실 거예요")
  → user_taste_vectors에 덮어쓰기 (즉시 경로의 단순 가중 평균을 정교한 클러스터 벡터로 업그레이드)
```

### 왜 이 구조인가

- **즉시 경로:** "입력 → 결과 체감" 해결. 피드백 남기면 바로 추천 변화
- **배치 경로:** 정교한 분석 (K-means, LLM) + 전체 데이터 동기화
- **둘 다 같은 테이블에 씀:** 배치가 즉시 계산을 자연스럽게 덮어쓰며 품질 향상
- **기존 파이프라인 패턴 확장:** 별도 서버 없이 GitHub Actions + Supabase RPC

---

## 취향 벡터 계산 — 단계적 진화

데이터 양에 따라 계산 방식이 자동 전환:

### 초기 (1~10권): 가중 평균

```
taste_vector = Σ(book_embedding[i] × weight[i]) / Σ(weight[i])
```

weight는 피드백 깊이 스코어 (아래 "추천 신뢰도" 참조):
- 읽음 표시만: 1.0
- + 호오 평가: 1.5
- + 감성태그: 2.0
- + 리뷰 텍스트: 3.0
- 최애 책(is_onboarding_favorite): × 1.2 보너스

호오가 'bad'인 책: negative weight (예: -0.5) → 반대 방향으로 벡터 이동

**결과:** 단일 취향 벡터 1개

### 중기 (10~20권): 2~3 클러스터 시도

- K-means (k=2~3) 시도
- 실루엣 스코어로 클러스터 품질 평가
- 품질이 임계값 미만이면 가중 평균 유지
- 통과하면 클러스터별 취향 벡터 저장

**결과:** 취향 벡터 1~3개 (클러스터별)

### 후기 (20권+): 본격 다축 프로파일링

- K-means (k=2~5, 최적 k 자동 탐색)
- 클러스터별 LLM 라벨 생성 ("SF 세계관", "잔잔한 에세이")
- 클러스터 크기에 비례한 가중치 → 추천 비율 조절

**결과:** 취향 벡터 2~5개 + 라벨 + 가중치

---

## 추천 신뢰도 스코어

고정값("5권이면 추천 가능")이 아니라, **데이터 질과 양을 종합 평가하는 스코어**.

### 입력 변수

```
confidence_score = f(
  feedback_depth_score,    # 피드백 깊이 합산
  genre_diversity,         # 장르 다양성
  rating_variance,         # 호오 분산 (전부 '좋다'면 구별력 낮음)
  book_count,              # 절대 권수
)
```

### 피드백 깊이 스코어 (책 1권당)

| 유저 입력 | 점수 |
|-----------|------|
| 읽음 표시만 | 1 |
| + 호오 평가 (good/neutral/bad) | 2 |
| + 감성태그 1~2개 | 3 |
| + 감성태그 3개+ | 4 |
| + 리뷰 텍스트 (50자+) | 5 |

### 장르 다양성

```
genre_diversity = unique_genres / total_books
```

같은 장르 5권보다 다른 장르 3권이 취향 벡터 정확도가 높을 수 있음.

### 호오 분산

```
rating_variance = 전부 같은 평가가 아닌 정도
```

전부 'good'이면 positive signal만 → 구별력 낮음. good + neutral + bad 섞여있으면 구별력 높음.

### 임계값과 단계

| 상태 | 조건 (가설, 실험으로 튜닝) | 유저에게 보이는 것 |
|------|---|---|
| **추천 불가** | confidence < threshold_low | "비슷한 책"만 (book-to-book) + 프로그레스 안내 |
| **초기 추천** | threshold_low ≤ confidence < threshold_high | 서재에 추천 섹션 활성화 (정확도 보통) |
| **정밀 추천** | confidence ≥ threshold_high | 추천 + 취향 프로필 + 추천 이유 |

**임계값은 실험으로 결정.** 테스트 유저 시나리오(1권만, 3권+태그, 10권+리뷰 등)를 만들어서 추천 결과 품질을 직접 확인하며 튜닝.

### 실시간 프로그레스 표시

신뢰도 스코어는 Supabase RPC로 즉시 계산 가능 (배치 의존 X):
- "감성태그를 남기면 추천이 더 정확해져요" (현재 depth가 낮은 책이 있을 때)
- "다른 장르 책도 추가해보세요" (genre_diversity가 낮을 때)
- "추천 준비 완료!" (threshold 넘었을 때)

---

## 데이터 흐름 전체도

```
[유저 사이드 — 계속 성장]
유저 → 책 등록/피드백 → user_books (rating, emotion_tags, review_text)
                          ↓ (Edge Function 트리거)
                     즉시: 가중 평균 취향 벡터 계산 → user_taste_vectors
                          ↓
                     즉시: 추천 신뢰도 스코어 갱신
                          ↓
                     앱에서 추천 결과 갱신

[우리가 쌓는 데이터 — 계속 성장]
알라딘 → books → Tier 1 임베딩 (daily)
YES24 → rich_description → Tier 2 임베딩 (daily)
정보나루 → library_keywords → Tier 2 보강 (daily)
                          ↓
                     배치: 새 임베딩 반영하여 전체 유저 취향 벡터 재계산
                     배치: LLM 취향 요약 / 추천 이유 갱신
```

양쪽 데이터가 모두 커질수록 추천 품질이 올라가는 구조.

---

## 인프라 구현 상세

### Supabase RPC 함수 (신규)

```sql
-- 1. book-to-book 유사도
match_books_by_similarity(book_id uuid, match_count int)
  → book_embeddings에서 코사인 유사도 Top-N 반환

-- 2. taste-to-book 추천
recommend_books_for_user(user_id uuid, match_count int)
  → user_taste_vectors × book_embeddings 코사인 유사도
  → user_books에 있는 책 제외
  → Top-N 반환

-- 3. 추천 신뢰도 스코어
calculate_recommendation_confidence(user_id uuid)
  → user_books에서 피드백 깊이, 장르 다양성, 호오 분산 계산
  → 스코어 반환
```

### Supabase Edge Function (신규)

```
on_feedback_submitted:
  → 유저의 읽은 책 임베딩 조회
  → 가중 평균 취향 벡터 계산
  → user_taste_vectors upsert
```

### GitHub Actions 배치 (신규)

```yaml
# daily-taste-recompute.yml (daily-embed-t2 후 또는 주 1회)
- 전체 유저 취향 벡터 재계산 (K-means for 충분한 데이터)
- LLM 취향 요약 생성
- 추천 이유 생성
```

### DB 변경 (신규 마이그레이션)

```sql
-- user_taste_vectors에 추가 컬럼
ALTER TABLE user_taste_vectors ADD COLUMN weight float DEFAULT 1.0;      -- 클러스터 크기 가중치
ALTER TABLE user_taste_vectors ADD COLUMN summary text;                   -- LLM 취향 요약
ALTER TABLE user_taste_vectors ADD COLUMN method text DEFAULT 'weighted_avg'; -- 'weighted_avg' | 'kmeans'

-- 추천 신뢰도 저장 (캐싱)
ALTER TABLE users ADD COLUMN recommendation_confidence jsonb;
-- 예: {"score": 0.72, "feedback_depth": 18, "genre_diversity": 0.6, "updated_at": "..."}
```

---

## 실험 계획

파라미터 튜닝을 위해 테스트 시나리오를 만들어 실험:

### 테스트 시나리오

| 시나리오 | 입력 | 검증 포인트 |
|----------|------|------------|
| 1권, 피드백 없음 | 읽음만 | book-to-book만 작동하는지 |
| 3권, 감성태그만 | rating + tags | 가중 평균 취향 벡터 품질 |
| 5권, 풀 피드백 | rating + tags + review | 추천 결과가 의미 있는지 |
| 10권, 다양한 장르 | 소설+에세이+SF | 클러스터링이 장르를 분리하는지 |
| 10권, 같은 장르 | 한국소설만 | 같은 장르 내 세부 취향 구별 |
| 1권 추가 전후 비교 | 기존 5권 + 1권 추가 | 추천 변화가 체감되는지 |

### 튜닝 대상

- 피드백 깊이 점수 배점 (1/2/3/4/5가 적절한지)
- 가중 평균 weight 값들 (호오, 태그, 리뷰, 최애 보너스)
- negative weight 크기 ('bad' 평가 책의 영향력)
- 추천 신뢰도 임계값 (threshold_low, threshold_high)
- 클러스터링 전환 기준 (10권? 15권?)
- K-means k 범위
- 실루엣 스코어 임계값

---

## 취향 프로필 (데이터 충분 시)

추천 섹션보다 나중에 활성화:

- **조건:** 클러스터링이 의미 있는 결과를 낼 때 (배치에서 판단)
- **내용:** "당신은 [클러스터 라벨] 타입의 독자예요" + 클러스터별 대표 책
- **LLM 생성:** 클러스터 내 책들의 메타데이터 + 유저 피드백을 종합하여 자연어 요약

---

## PRODUCT_PLAN Phase 구조 변경

### 기존
```
Phase 1: 서재 + 피드백 수집
Phase 2: 취향 프로필
Phase 3: 추천 엔진
```

### 변경
```
Phase 1 (MVP): 서재 + 피드백 수집 + 추천 (book-to-book + taste-to-book) + 온보딩
Phase 2: 취향 프로필 (LLM 요약) + 추천 이유 + 클러스터 라벨 자동 생성
Phase 3: 카테고리 확장 (영화, 뮤지컬, 전시 등)
```

추천은 MVP 필수. 취향 프로필 "표시"는 데이터 충분할 때 자연스럽게 활성화.

---

## 다음 단계

1. 이 스펙 리뷰 → 확정
2. PRODUCT_PLAN.md Phase 구조 업데이트
3. 온보딩 스펙 미정 항목 확정 (Step 4, Empty State, 프로그레스)
4. 구현 플랜 작성
5. 테스트 시나리오 실행 → 파라미터 튜닝
