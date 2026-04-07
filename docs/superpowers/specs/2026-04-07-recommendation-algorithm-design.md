# 추천 알고리즘 설계 (Cold/Warm Tier 시스템)

> 2026-04-07 | Eden
> 관련 spec: `2026-04-07-data-collection-design.md`, `2026-04-07-curation-system-design.md`
> 대체: `2026-04-07-recommendation-cold-warm-design.md` (분할됨)

## 1. 배경

18 페르소나 + 50 랜덤 + 4-mode 검증 결과:

1. v3 엔진 L1/L2 임베딩은 binary처럼 작동(cosine 1.0/0.3 양극화) — 가중치 효과 없음
2. desc는 정상 분포이며 가장 변별력 있는 신호
3. content-only 알고리즘은 cold start 한계가 본질적 — Netflix/Spotify도 동일
4. 신규 서비스 단계에서 CF는 데이터가 없어 불가능. **유저 행동이 쌓이는 만큼 점진적 hybrid로 전환**

## 2. 두 축 정의

### Cold (Stage 0) — 출시부터 활성 100명까지
- **알고리즘**: H10_no_l1 (content-based)
- **적용 범위**: Tier 2 유저(좋아요 6권+)에게만 적용. 그 외는 큐레이션 시스템 (별도 spec)

### Warm (Stage 1~3) — 활성 100명+
- **알고리즘**: Hybrid (content + Item-to-Item Collaborative Filtering)
- **점진적 활성화**: 활성 유저 수 + co-occurrence 데이터 밀도에 따라 자동 stage 전환
- **알고리즘은 Tier 2 유저에게만 적용**되는 건 동일

## 3. 두 layer 구조

이 spec은 두 층으로 나뉨:

| Layer | 내용 | 결정 시점 |
|---|---|---|
| **Layer 1 — 확정** | 구조, 알고리즘, 변경 메커니즘 | 지금 |
| **Layer 2 — 변수** | 정확한 임계값, 가중치, 비율 | 출시 후 실측 |

Layer 2 항목은 spec에 **변수명만** 박고, 초기값은 "추정"으로 명시. 운영 후 데이터로 조정.

## 4. Tier 시스템 (Layer 1 구조)

유저의 데이터 양에 따라 추천 방식 변경.

```
Tier 0 — 데이터 없음
  조건: 좋아요(good) < TIER1_THRESHOLD
  추천: 알고리즘 사용 안 함. 큐레이션만 노출.
  표시: "당신을 위한 추천" 섹션 없음

Tier 1 — 약한 추천 (similar 엔진)
  조건: TIER1_THRESHOLD ≤ 좋아요 < TIER2_THRESHOLD
  추천: book-to-book similar 엔진 (단순 desc 유사도)
  표시: "당신이 좋아한 ○○과 비슷한 책"

Tier 2 — 본 추천 (H10 또는 Hybrid)
  조건: 좋아요 ≥ TIER2_THRESHOLD
  추천: H10_no_l1 (Stage 0) 또는 Hybrid (Stage 1+)
  표시: "당신을 위한 추천"
```

### 임계값 (Layer 2 — 추정값, 실측 후 조정)

| 변수 | 초기 추정값 | 근거 |
|---|---|---|
| TIER1_THRESHOLD | 3 | 18 페르소나 테스트에서 author hit이 의미 있게 시작되는 최소 |
| TIER2_THRESHOLD | 6 | H10 풀 알고리즘이 안정적으로 작동하는 최소 |

**출시 후 30일 데이터 보고 조정.** 실측 시 너무 높으면(유저들이 도달 못 함) 낮추고, 너무 낮으면(추천 품질 떨어짐) 올림.

### bad만 있는 경우
- good=0, bad>0 → Tier 0 (큐레이션만)
- good 카운트만 Tier 결정 기준

## 5. 알고리즘 — Tier별

### 5.1 Tier 1 — Similar 엔진

**입력**: 유저가 좋아한 책 1개 (가장 최근)
**출력**: desc cosine 유사도 Top N

```python
def similar_recommendations(user_book_id, top_n=20):
    target = index.get_book(user_book_id)
    scores = {}
    for cid in index.book_ids:
        if cid == user_book_id:
            continue
        cand = index.get_book(cid)
        scores[cid] = float(np.dot(target.desc, cand.desc))
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
```

**왜 desc만?**: 1권 데이터로는 reason/fb_desc 매칭 의미 없음. desc만으로 충분히 정확.

### 5.2 Tier 2 — H10_no_l1 (Stage 0)

```python
# 가중치
W_REASON  = 2.0
W_DESC    = 3.0
W_L1      = 0.0   # 제거 (binary 효과)
W_L2      = 0.0   # 제거
W_FB_DESC = 2.0

score = (W_REASON * reason_score
       + W_DESC * desc_score
       + W_FB_DESC * fb_desc_score)

# 후처리: cap_dynamic
def cap_dynamic(scored, meta, persona, top_n=20):
    """유저가 읽은 L1 분포 비율을 추천에 반영."""
    user_l1_dist = Counter(parse_l1(meta[b]['genre']) for b in good_book_ids)
    total = sum(user_l1_dist.values())
    quotas = {l1: max(1, round(cnt/total * top_n)) for l1, cnt in user_l1_dist.items()}
    # quota 따라 선택
```

**검증**: 18 페르소나 (omnivore/maniac/minimal/negative/normal 5그룹) 모두 통과. 50 랜덤 페르소나 88%에서 baseline 대비 우수.

### 5.3 Tier 2 — Hybrid (Stage 1~3)

```python
# Stage 1
score = content_score + α1 * co_occurrence_score

# Stage 2
score = w_content * content_score + w_cf * cf_score
# (w_content + w_cf = 1.0)

# Stage 3
# 유저별 동적 가중치 (좋아요 권수에 따라)
```

**Score Normalization** (Layer 1 — 결정 사항):
- content_score: H10 결과를 min-max normalize → 0~1
- cf_score: co_occurrence 기반 score → 0~1
- 두 score 같은 단위에서 가중합

**Stage별 가중치 (Layer 2 — 추정)**:

| Stage | 활성 유저 | co_pair (≥3) | content 가중치 | CF 가중치 |
|---|---|---|---|---|
| 0 | < 100 | - | 1.0 | 0.0 |
| 1 | 100~ | 200~ | 0.8 | 0.2 (additive: α1=0.2) |
| 2 | 300~ | 1,000~ | 0.6 | 0.4 |
| 3 | 500~ | 3,000~ | 0.4 | 0.6 |

**Stage 임계값은 Layer 2.** 출시 후 데이터 밀도 보고 조정.

## 6. Stage 자동 전환 (Layer 1 메커니즘)

매일 00:00 cron 작업:

```
1. 활성 유저 수 측정 (30일 내 like/save/review 1건 이상)
2. co_occurrence 페어 수 측정 (co_like_count ≥ 3)
3. 다음 stage 임계 충족 여부 확인
4. 충족 시 → 자동 stage 전환 + 알림
```

### 자동 롤백 조건 (Layer 1 메커니즘)

새 stage 진입 후 7일간 모니터링:
- 추천 클릭률(CTR) 측정
- 직전 stage 대비 상대 변화 계산
- **CTR이 ROLLBACK_THRESHOLD 이상 떨어지면 자동 롤백 + 알림**
- 통계 유의성: 최소 1,000건 노출 이후 측정

**ROLLBACK_THRESHOLD (Layer 2)**: 초기 추정 -20%. 실측 후 조정.

## 7. Item-to-Item CF 알고리즘 (Layer 1)

**Amazon 스타일** — 단순하고 작은 도서 풀에 적합.

```python
def cf_score(user_book_ids, candidate_id, co_occurrence_matrix):
    """유저가 좋아한 책들과 후보 책의 co_like 점수 합산."""
    score = 0.0
    for liked_id in user_book_ids:
        pair_count = co_occurrence_matrix.get((liked_id, candidate_id), 0)
        if pair_count >= MIN_PAIR_COUNT:
            score += pair_count / pair_normalizer  # normalize
    return score / len(user_book_ids)  # 평균
```

**MIN_PAIR_COUNT (Layer 2)**: 초기 3. Stage 1=3, Stage 2=5, Stage 3=10. 데이터 노이즈 차단.

## 8. 로딩 성능

### 원칙
- **세션 동안 추천 결과 고정** (앱 메모리/state에 저장)
- **재진입/새로고침 시에만 갱신**
- **유저 액션(좋아요/저장)은 비동기 처리** — 응답 안 기다림

### 흐름
```
1. 유저 홈 진입 → /recommend API 호출
   - 캐시 hit이면 즉시 반환
   - cache miss이면 계산 후 캐시 저장
2. 추천 결과를 client state에 저장 → 세션 동안 유지
3. 유저 좋아요 누름 → DB 쓰기 → 백그라운드 워커에 재계산 큐 등록 → 즉시 응답
4. 화면은 안 변함 (다음 새로고침 시 새 추천 반영)
```

### 캐시 전략
- **Tier 2 유저**: 개인 캐시 (TTL 1시간 OR 좋아요 액션 시 무효화)
- **Tier 1 유저**: similar 엔진은 가벼우니 매 요청 계산
- **응답 목표**: < 200ms

### 캐시 인프라
- 1차: Postgres (이미 있음, 추가 비용 0)
- 부하 늘어나면 → Redis 도입 고려 (Layer 2)

## 9. UI Tier별 표시 (관련 spec과 연결)

각 Tier별 화면 구성은 큐레이션 spec에서 정의. 이 spec에선 **알고리즘 결과를 어떻게 표시할지**만:

### Tier 1 — Similar
```
"○○과 비슷한 책"  ← 좋아한 책 제목 표시
[가로 스크롤 카드]
```

### Tier 2 — H10 / Hybrid
```
"당신을 위한 추천"
[세로 리스트, 페이징]
```

### Tier 진행 안내 (CTA)
```
Tier 0: "좋아요 N권 더 누르면 비슷한 책을 찾아드려요"
Tier 1: "좋아요 N권 더 평가하면 취향 추천이 시작돼요"
```

## 10. 모니터링 (Layer 1 항목)

매일 자동 측정:

| 지표 | 목적 |
|---|---|
| Tier 분포 | 0/1/2 유저 수 |
| Tier 0→1, 1→2 전환율 | 임계가 적절한지 |
| Tier 2 추천 CTR | H10 효과 |
| 활성 유저 수 | Stage 전환 트리거 |
| co_pair (≥3) 수 | Stage 전환 트리거 |
| Stage 전환 후 7일 CTR | 자동 롤백 트리거 |

대시보드로 시각화 (별도 인프라 spec에서).

## 11. 책 cold start

신규 책(평점 0개)은 어떻게 추천에 잡히나?

- **Tier 2 추천**: H10은 desc 기반이라 신규 책도 자동으로 후보에 들어감 (기존 책과 desc 유사도 계산)
- **Tier 1 similar**: 동일
- **CF (Stage 1+)**: co_pair 0이라 자연스럽게 가산점 없음. content가 보충
- **신간 boost**: **하지 않음** (Eden 의견 — 신간에 관심 있는 사람 적음. 큐레이션 spec에서 별도 처리)

## 12. 범위 외

- **큐레이션 시스템** → 별도 spec
- **데이터 수집 인프라** → 별도 spec
- **wishlist UI 분리** → 별도 product spec
- **A/B 테스트** → 하지 않음 (정해진 방식 진행)
- **수동 활성화** → 하지 않음 (모든 stage 자동)
- **시간대별 추천** → 하지 않음
- **소셜 신호 (친구 활동)** → Phase 2+

## 13. 핵심 원칙

1. **Cold에서 출발하되 Warm 데이터를 첫날부터 수집** (별도 spec)
2. **알고리즘 변경은 자동 + 가역적** (롤백 항상 가능)
3. **임계값/가중치는 Layer 2 변수** — 실측으로 조정
4. **품질 기준을 낮추지 않음** — Tier 임계는 데이터 충분성 보장 기준
5. **유저 cold start는 큐레이션이, 책 cold start는 content가 처리**

## 14. 출시 후 결정 항목 (Layer 2 명시)

| 항목 | 초기 추정 | 조정 시점 |
|---|---|---|
| TIER1_THRESHOLD | 3 | 출시 30일 후 |
| TIER2_THRESHOLD | 6 | 출시 30일 후 |
| Stage 1 활성 유저 임계 | 100 | 활성 50명 시점 |
| Stage 1 co_pair 임계 | 200 | 활성 50명 시점 |
| ROLLBACK_THRESHOLD | -20% | 첫 stage 전환 후 |
| MIN_PAIR_COUNT | 3 | Stage 1 진입 후 |
| 캐시 TTL | 1시간 | 부하 측정 후 |
| Stage 2/3 가중치 | 0.6/0.4, 0.4/0.6 | 각 Stage 전환 후 |

각 항목은 **추측이 아니라 측정으로 결정**. 출시 전엔 추정값으로 시작.
