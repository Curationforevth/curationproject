# 추천 서빙 최적화 설계

> 2026-04-13 | Eden
> 관련 spec: `2026-04-07-recommendation-algorithm-design.md`
> 범위: build_index 속도, 서빙 레이턴시, 메모리, Supabase egress

## 1. 배경

### 현재 문제

| 문제 | 현재 수치 | 목표 |
|------|----------|------|
| **서빙 레이턴시** | 17~61초 (2,679권) | < 200ms |
| **빌드 시간** | ~12분 (38K reasons, 76 API 호출) | < 3분 |
| **메모리** | 186MB (2,679권) → 3.4GB (5만권) | Render free tier 512MB |
| **Supabase egress** | 매일 970MB, 월 28.4GB | 5.5GB/월 무료 한도 |

### 근본 원인

1. **`recommend_scores()`**: 전체 N권을 Python for-loop으로 순회하며 `_score_one()` 호출. O(N × n_good × reasons²)
2. **`_score_one()` 내부**: 매 호출마다 `np.stack()` 반복 (리스트 → ndarray 변환). 이것이 **실행 시간의 90%**
3. **`build_index.py`**: 38K reason rows를 500개씩 76번 API 호출. PAGE_SLEEP 포함 ~12분
4. **매일 전체 재구축**: 변경 유무와 무관하게 DB 전체를 읽음 → egress 폭발

## 2. 벤치마크 결과 요약

5회(v1~final) 벤치마크로 검증. 벤치마크 스크립트: `recommendation-server/scripts/benchmark_*.py`

> **가중치 주의**: v1~v4 벤치마크는 현재 config.py 가중치(W_R=1.0, W_D=0.5, W_L1=3.0, W_L2=1.0)로 실행.
> `benchmark_final.py`만 H10_no_l1 가중치(W_R=2.0, W_D=3.0, W_L1=0.0, W_L2=0.0)로 실행.
> 아래 섹션 2.2의 recall 수치 중 "H10 가중치" 표기가 있는 것은 final 벤치마크 결과.
> 표기 없는 것은 default 가중치 결과.

### 2.1 핵심 발견

**Pre-stacked reasons (np.stack 사전 계산)**이 유일하게 유효한 최적화:

| 방식 | 20good 500c | 정확성 |
|------|------------|--------|
| 현재 (원본 loop) | 10,024ms | baseline |
| v2 batch (내부 벡터화) | 702ms | max diff 0.0006 |
| **v4 prestacked batch** | **191ms** | **max diff 0.0006** |
| v3 3D 패딩 텐서 | 1,779ms | PASS (더 느림) |

**시도했으나 효과 없었던 것:**
- 3D 패딩 텐서 (einsum): 2.5x 느림. 패딩 + 메모리 할당 오버헤드
- float16 런타임 연산: f32보다 느림. CPU에서 f16→f32 캐스팅 비용
- H10_no_l1 가중치로 L1/L2 제거: 속도 3% 개선 (무의미)

### 2.2 Two-stage 검증

**Stage 1: Hybrid (single-query ∪ per-book)**

| 방식 | 6good | 10good | 20good |
|------|-------|--------|--------|
| single-query 500 | recall 100% | 100% | **85%** |
| per-book 500 | recall **55%** | 95% | 100% |
| **hybrid 500** | **100%** | **100%** | **100%** |

hybrid가 양쪽의 약점을 보완. Stage 1 레이턴시: 6~36ms.

**Stage 2 후보 수 vs recall (H10 가중치, 50명 랜덤 + 30명 클러스터)**

| 후보 수 | 랜덤 avg | 랜덤 min | 클러스터 avg | 클러스터 min |
|---------|---------|---------|-------------|-------------|
| 500 | 93% | 70% | **98%** | **90%** |
| 700 | 97% | 85% | **99%** | **90%** |
| 1000 | 99% | 90% | - | - |

클러스터 유저(실제 유저에 가까움) 500 cands에서 min 90%. 랜덤 유저는 취향이 완전히 흩어진 극단 케이스.

### 2.3 float16 저장

| 항목 | 결과 |
|------|------|
| 정확성 | max diff **0.000000**, top-20 순위 **20/20** 일치 |
| 메모리 절감 | prestacked f32 271MB → f16 **136MB** (50%) |
| 런타임 | f16 저장 → f32 캐스팅하여 연산 (f16 직접 연산은 더 느림) |

### 2.4 Supabase egress

| 시나리오 | 일 | 월 |
|---------|-----|-----|
| 현재 (전체 재구축) | 970MB | **28.4GB** |
| **증분 업데이트** | 32MB | **0.94GB** |
| 절감 | | **97%** |

## 3. 설계

### 3.1 아키텍처 개요

```
[유저 좋아요/싫어요]
        │
        ▼
  /feedback API ──→ DB 저장 ──→ 비동기 재계산 큐 등록
                                      │
                                      ▼
                              백그라운드 워커
                              (Stage 1 + Stage 2)
                                      │
                                      ▼
                              recommendation_cache 테이블
                                      │
[유저 홈 진입]                          │
        │                              │
        ▼                              │
  /recommend API ──→ 캐시 hit? ────────┘
        │                    yes → 즉시 반환 (0ms)
        │
        └── no → on-demand 계산 (500~700ms) → 캐시 저장 → 반환
```

### 3.2 인덱스 구조 변경

**현재 index.pkl:**
```python
{
    "index": VectorIndex,      # _books: {bid: BookVectors(reasons, desc, l1, l2)}
    "meta": books_meta,        # {bid: {title, author, cover_url}}
    "built_at": str,
    "version": "v3-float16",
}
```

**변경 후 index.pkl:**
```python
{
    "index": VectorIndex,
    "meta": books_meta,
    "built_at": str,
    "version": "v4-prestacked",

    # Stage 1용 (서버 startup 시 메모리 상주)
    "desc_matrix_f16": np.ndarray,         # (N, 2000) float16
    "agg_reason_matrix_f16": np.ndarray,   # (N, 2000) float16
    "bid_order": list[str],                # 행렬 인덱스 ↔ book_id 매핑

    # Stage 2용 (서버 startup 시 메모리 상주, f16 저장 → 사용 시 f32 캐스팅)
    "prestacked_reasons_f16": dict[str, np.ndarray],  # {bid: (n_reasons, 2000) float16}
}
```

**메모리 사용량 (2,679권):**

| 항목 | 크기 |
|------|------|
| VectorIndex (기존) | 166MB |
| desc_matrix (f16) | 10MB |
| agg_reason_matrix (f16) | 10MB |
| prestacked_reasons (f16) | 136MB |
| **합계** | **322MB** |

기존 VectorIndex의 reasons 리스트와 prestacked_reasons_f16이 중복이므로, VectorIndex에서 reasons 리스트를 제거하면 **~186MB**로 줄일 수 있음. 이건 구현 단계에서 결정.

### 3.3 Stage 1 — Hybrid 후보 선별

**목적**: 전체 N권에서 추천 후보 K권을 빠르게 선별.

**알고리즘**: single-query 스코어와 per-book 스코어를 min-max 정규화 후 합산.

```python
def stage1_hybrid(liked_books, fb_data, top_n=700):
    # --- single-query ---
    # desc: max(cos(good_desc, all_desc)) per book
    # reason: max(cos(good_agg_reason, all_agg_reason)) per book
    # fb_desc: Σ sign × cos(fb_emb, all_desc)
    sq_scores = 3.0 * sq_desc + 2.0 * sq_reason + 2.0 * sq_fb

    # --- per-book ---
    # good book 각각의 desc/agg_reason과 전체 책의 유사도 누적
    # bad book 감점, fb_desc 가산
    pb_scores = Σ(good) - Σ(bad) + Σ(fb)

    # --- 합산 ---
    sq_norm = min_max_normalize(sq_scores)
    pb_norm = min_max_normalize(pb_scores)
    combined = sq_norm + pb_norm
    return top_n(combined, exclude=read_ids)
```

**성능**: 6~36ms (numpy 행렬곱, O(N × dim)).
**레이턴시 스케일**: N에 비례. 5만권에서 ~170ms.

### 3.4 Stage 2 — Prestacked 배치 스코어링

**목적**: Stage 1 후보에 대해 현재 `_score_one()` 로직을 **정확히** 적용.

**변경점**: `np.stack()` 호출을 제거. prestacked ndarray를 직접 사용.

```python
def batch_score_prestacked(index, liked_books, fb_data, candidate_ids):
    # desc, l1, l2, fb_desc: 행렬 일괄 연산 (기존 v2와 동일)
    # reason: 후보별 루프 유지 (가변 길이)
    #   - cand_r = prestacked_reasons[cid].astype(np.float32)  # f16→f32
    #   - query_r = prestacked_reasons[bid].astype(np.float32)
    #   - sims = query_r @ cand_r.T  ← np.stack() 없이 직접 matmul
    #   - _maxsim 로직 동일: sims.max(axis=1).mean()
```

**변경하지 않는 것:**
- _maxsim 로직 (max per query reason → mean across query reasons)
- FB_REASON_WEIGHT / REASON_WEIGHT_WITH_FB / REASON_WEIGHT_WITHOUT_FB 가중치
- good/bad 분리 + 부호 반전
- 5개 신호 가중 합산

**정확성**: 원본 `_score_one()` 대비 max diff 0.0006. top-20 순위 완전 일치.

### 3.5 캐싱 전략

#### recommendation_cache 테이블

```sql
CREATE TABLE recommendation_cache (
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    recommendations JSONB NOT NULL,
    -- [{book_id, score, title, author, cover_url}, ...]
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    good_count INT NOT NULL,
    bad_count INT NOT NULL,
    has_feedback BOOLEAN NOT NULL DEFAULT false,
    input_hash TEXT NOT NULL,
    -- SHA256(sorted good_ids + sorted bad_ids + sorted fb_ids)
    -- 입력이 바뀌었는지 판단
    PRIMARY KEY (user_id)
);
```

#### 캐시 히트 조건

캐시된 결과를 반환하려면 **다음 모두 충족**:

1. `recommendation_cache` 에 해당 user_id row 존재
2. `input_hash`가 현재 user_books 상태와 일치
3. `computed_at`이 마지막 인덱스 빌드 이후

하나라도 불일치하면 **stale** → on-demand 재계산.

#### 캐시 무효화 시점

| 이벤트 | 동작 |
|--------|------|
| 유저가 좋아요/싫어요 | input_hash 변경 → 자동 stale |
| 유저가 피드백 작성 | input_hash 변경 → 자동 stale |
| 인덱스 재빌드 (새 책 추가) | computed_at < built_at → 자동 stale |

#### 왜 TTL이 아니라 input_hash인가

- TTL 기반: "1시간마다 만료" → 변경 없어도 재계산 (낭비), 변경 있어도 1시간 대기 (지연)
- **input_hash 기반**: 입력이 바뀔 때만 재계산. 변경 없으면 영원히 유효. 변경 있으면 즉시 반영.

### 3.6 비동기 사전 계산

#### 트리거

유저가 `/feedback` 또는 좋아요/싫어요 액션을 하면:

1. DB에 user_books 저장 (기존 동작, 즉시 응답)
2. **백그라운드 task로 추천 재계산 등록**

#### 구현 방식

FastAPI의 `BackgroundTasks`를 사용:

```python
@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(verify_jwt),
):
    # ... 기존 DB 저장 로직 ...

    # 비동기 재계산 등록
    background_tasks.add_task(recompute_recommendations, current_user)

    return FeedbackResponse(status="ok")
```

#### 재계산 함수

```python
def recompute_recommendations(user_id: str):
    """백그라운드에서 실행. 유저의 추천을 재계산하고 캐시에 저장."""
    # 1. user_books 로드
    # 2. input_hash 계산
    # 3. 캐시 확인 — 이미 최신이면 skip
    # 4. Stage 1 → Stage 2 → top-50 저장
    # 5. recommendation_cache upsert
```

**top-50을 저장하는 이유**: 클라이언트가 limit=10~20으로 요청하지만, 이미 본 책 필터링 등으로 줄어들 수 있으므로 여유분 확보.

#### 동시성 보호

같은 유저가 빠르게 여러 번 좋아요 → 여러 재계산 task가 겹칠 수 있음.

- **단순한 해법**: `recommendation_cache`에 `computing` boolean 추가. task 시작 시 True, 완료 시 False.
- 이미 computing=True면 새 task는 skip.
- **skip된 재계산의 처리**: 다음 `/recommend` 요청 시 input_hash 불일치 → on-demand 재계산이 자동으로 최신 상태를 반영. 별도 retry 메커니즘 불필요.
- 더 복잡한 큐 시스템은 유저 수가 100+ 될 때 고려.

### 3.7 /recommend API 흐름 변경

```python
@router.get("/recommend/{user_id}")
async def get_recommendations(user_id, request, limit, background_tasks):
    index = request.app.state.index
    books_meta = request.app.state.books_meta

    # 1. user_books 로드
    sb = get_supabase()
    ub_res = sb.table("user_books").select(...).eq("user_id", user_id).execute()

    # 2. input_hash 계산
    input_hash = compute_input_hash(ub_res.data)

    # 3. 캐시 확인
    cache = sb.table("recommendation_cache").select("*") \
        .eq("user_id", user_id).single().execute()

    if cache.data and cache.data["input_hash"] == input_hash \
       and cache.data["computed_at"] > request.app.state.built_at:
        # 캐시 히트 → 즉시 반환
        return format_response(cache.data["recommendations"][:limit])

    # 4. 캐시 미스 → on-demand 계산
    liked_books, fb_data = parse_user_books(ub_res.data)
    # Stage 1/2에 필요한 행렬은 app.state에서 가져옴
    desc_matrix = request.app.state.desc_matrix_f16
    agg_matrix = request.app.state.agg_reason_matrix_f16
    prestacked = request.app.state.prestacked_reasons_f16
    bid_order = request.app.state.bid_order

    candidates = stage1_hybrid(
        liked_books, fb_data, desc_matrix, agg_matrix, bid_order, top_n=700)
    scores = batch_score_prestacked(
        index, liked_books, fb_data, candidates, prestacked)
    top_recs = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:50]

    # 5. 캐시 저장 (비동기, conditional upsert)
    # input_hash가 여전히 일치할 때만 저장 — 계산 중 새 좋아요가
    # 들어왔다면 stale 결과를 덮어쓰지 않음
    background_tasks.add_task(
        save_cache_if_current, user_id, top_recs, input_hash, ...)

    # 6. 응답
    return format_response(top_recs[:limit])
```

### 3.8 build_index 변경

#### 증분 업데이트

```python
def build(incremental: bool = True):
    if incremental and existing_index_exists():
        # 마지막 빌드 이후 변경된 책만 fetch
        last_built = load_existing_built_at()
        changed_books = fetch_changed_since(last_built)
        changed_reasons = fetch_changed_reasons_since(last_built)

        # 기존 인덱스 로드 → 변경분 merge
        index, meta, _ = load_index()
        apply_changes(index, changed_books, changed_reasons)
    else:
        # 전체 재구축 (기존 로직)
        ...

    # prestacked 행렬 구축
    prestacked_f16 = build_prestacked(index)
    desc_matrix_f16 = build_desc_matrix(index)
    agg_reason_f16 = build_agg_reason_matrix(index)

    # 저장
    bundle = {
        "index": index, "meta": meta, ...,
        "prestacked_reasons_f16": prestacked_f16,
        "desc_matrix_f16": desc_matrix_f16,
        "agg_reason_matrix_f16": agg_reason_f16,
        "bid_order": bid_order,
    }
```

**변경 감지**: Supabase의 `updated_at` 컬럼 활용.
- `books`: `updated_at > last_built_at`
- `book_v3_vectors`: `updated_at > last_built_at`
- `book_love_reasons`: `updated_at > last_built_at`

`updated_at` 컬럼이 없는 테이블은 migration으로 추가 (trigger 기반 자동 갱신).

**주기적 전체 재구축**: 주 1회 수동 트리거 (`build-index.yml`). 증분 업데이트 drift 방지.

#### Pagination 최적화

- `PAGE_SIZE_VECTOR`: 500 → **1000** (PostgREST 기본 max 확인 후)
- 4개 테이블 **병렬 fetch** (`concurrent.futures.ThreadPoolExecutor`)
- `PAGE_SLEEP` 제거 (read-only, rate limit 불필요)

## 4. 품질 검증

### 4.1 스코어링 로직 — 변경 없음

`_score_one()` 의 5개 신호 결합 로직은 **한 줄도 변경하지 않음**:

| 신호 | 연산 | 변경 |
|------|------|------|
| reason_score | _maxsim(query_reasons, cand_reasons) per good/bad book → 가중 평균 | 없음 |
| desc_score | max(cos(good_descs, cand_desc)) | 없음 |
| l1_score | max(cos(good_l1s, cand_l1)) | 없음 |
| l2_score | max(cos(good_l2s, cand_l2)) | 없음 |
| fb_desc_score | mean(sign × cos(fb_emb, cand_desc)) | 없음 |

변경된 것은 **np.stack()을 사전 계산**한 것뿐. matmul 입력이 동일하므로 출력도 동일.

정확성 검증: max diff 0.0006 (float32 연산 순서 차이에 의한 부동소수점 오차).

### 4.2 Stage 1 필터링으로 인한 recall 손실

**이것이 유일한 품질 리스크.**

Stage 1이 걸러낸 책은 Stage 2에서 스코어링되지 않으므로, 최종 추천에서 빠질 수 있음.

**벤치마크 결과 (H10 가중치)**:

| 유저 타입 | 500 cands | 700 cands |
|-----------|----------|----------|
| 클러스터 유저 (1~3 장르, 30명) | avg 98%, **min 90%** | avg 99%, min 90% |
| 랜덤 유저 (50명) | avg 93%, min 70% | avg 97%, min 85% |

**판단**:
- 실제 유저는 취향이 있으므로 클러스터 유저에 가까움
- 클러스터 유저 500 cands에서 min 90% — **top-20 중 최소 18개 일치**
- 놓치는 2개는 Stage 1에서 desc+agg_reason 유사도가 낮았던 책 (유저 취향의 "변두리")
- **이 2개가 유저에게 중요한 추천이었을 확률**: 낮음 (점수 순위 18~20위)

### 4.3 캐시로 인한 품질 영향

**시나리오**: 유저가 좋아요 → 홈 화면에 아직 이전 추천이 보임 → 새 추천 반영 시점?

| 시나리오 | 지연 | 수용 가능? |
|---------|------|-----------|
| 좋아요 후 홈 유지 | 추천 안 변함 (스펙 의도) | ✓ 스펙 섹션 8: "세션 동안 고정" |
| 좋아요 후 앱 재진입 | 비동기 완료 시 즉시 반영 (~700ms) | ✓ |
| 좋아요 후 즉시 새로고침 | 비동기 미완료 → on-demand 계산 | ✓ ~700ms 대기 |
| 인덱스 재빌드 직후 | computed_at < built_at → stale → 재계산 | ✓ |

**문제 없음.** 스펙이 이미 "세션 동안 고정"을 의도했으므로, 캐싱은 스펙과 정합.

### 4.4 증분 빌드로 인한 인덱스 품질

**리스크**: 증분 업데이트가 누적되면 전체 재구축 대비 drift 발생 가능.

**원인**:
- 삭제된 책이 인덱스에 남아 있을 수 있음
- `updated_at` 누락 (trigger 설정 전 변경된 데이터)
- 장르 임베딩 변경 시 증분에서 감지 안 됨

**대책**:
- 주 1회 전체 재구축 (수동 `build-index.yml`)
- 증분 빌드 후 skip ratio guard 적용 (기존 5% 임계값)
- 전체 재구축 대비 인덱스 크기 차이가 2% 이상이면 경고

### 4.5 cap_dynamic과의 관계

스펙 5.2의 `cap_dynamic` (L1 분포 비율로 추천 재조정)은 **Stage 2 이후** 후처리로 적용.

벤치마크에서 cap_dynamic 적용 시:
- 장르 다양성: 7→11 (6good), 9→13 (20good)
- top-20 중 5~12개가 교체됨

**cap_dynamic은 recall 측정의 ground truth 자체를 변경하므로, recall 수치와 직접 비교 불가.** cap_dynamic이 적용되면 "정답 자체"가 달라지므로, Stage 1 필터링과는 독립적인 품질 차원.

### 4.6 알고리즘 진화에 대한 제약

**Stage 1이 걸러낸 책은 Stage 2에서 볼 수 없다** — 이것이 Two-stage의 본질적 제약.

현재 Stage 1은 desc + agg_reason + fb_desc 3개 신호로 필터링. 만약 미래에:

| 변경 | Stage 1 영향 | 대응 |
|------|-------------|------|
| 가중치 조정 (Layer 2) | 없음 — Stage 2에서만 적용 | 자동 |
| 새 신호 추가 (CF, 소셜 등) | Stage 1이 못 잡을 수 있음 | Stage 1에 신호 추가 |
| _maxsim 로직 변경 | Stage 2만 변경, Stage 1 무관 | 자동 |
| Warm stage 전환 (Hybrid) | CF 점수가 Stage 1에 없음 | **Stage 1에 CF 신호 추가 필요** |

**Warm stage 전환 시 Stage 1 수정이 필요하다**는 점을 설계 시점에 명시.

## 5. 스케일링

### 5.1 레이턴시

| 규모 | Stage 1 | Stage 2 (700c) | 합계 | 서빙 방식 |
|------|---------|---------------|------|----------|
| 2,679권 | 10ms | 200ms | **210ms** | on-demand 가능 |
| 5,000권 | 18ms | ~200ms | **~220ms** | on-demand 가능 |
| 10,000권 | 36ms | ~200ms | **~240ms** | on-demand 가능 |
| 30,000권 | 107ms | ~200ms | **~310ms** | 캐시 권장 |
| 50,000권 | 178ms | ~200ms | **~380ms** | 캐시 필수 |

Stage 2는 후보 수 고정이므로 N에 무관. Stage 1만 O(N).

**주의**: 위 수치는 로컬 Mac (Apple Silicon) 기준. Render free tier (0.5 vCPU shared)에서는 numpy BLAS 연산이 **3~5x 느릴 수 있음** (CPU 스로틀링 + 스레드 경합). 예: 210ms → 630~1050ms.
캐싱이 이를 흡수하므로 아키텍처 변경 불필요. 다만 on-demand fallback(캐시 미스) 시 유저 체감 대기가 ~1초일 수 있음.
→ 프로덕션 배포 후 실측 필요. 실측 결과에 따라 후보 수 조정.

### 5.2 메모리

| 규모 | desc+agg (f16) | prestacked (f16) | VectorIndex | 합계 | Render |
|------|---------------|-----------------|-------------|------|--------|
| 2,679권 | 20MB | 136MB | 166MB | **322MB** | Free |
| 5,000권 | 38MB | 253MB | 310MB | **~600MB** | Starter ($7/mo) |
| 10,000권 | 76MB | 506MB | 620MB | **~1.2GB** | Pro ($25/mo) |

**5천권 이후 Render 유료 전환 필수.** 이건 이 설계의 한계가 아니라, 2000차원 벡터를 메모리에 올리는 모든 방식의 한계. FAISS mmap 등으로 개선 가능하지만 별도 과제.

### 5.3 pkl 크기

| 규모 | 크기 | Git LFS |
|------|------|---------|
| 2,679권 | 336MB | 무료 1GB 이내 |
| 5,000권 | ~628MB | 무료 한도 주의 |
| 10,000권 | ~1.3GB | **LFS 유료 필요** |

**1만권 이후**: pkl을 git에서 제거하고, 빌드 시 직접 생성하거나 S3/GCS에서 다운로드하는 방식으로 전환.

## 6. Supabase egress 대응

### 6.1 즉시 (4/15까지) — ✅ 완료 (2026-04-14)

- ~~불필요한 build_index 실행 중단 (수동 트리거만)~~
- `daily-pipeline.yml`의 `build-and-recompute` job 주석 처리 완료
- discovery, collect, enrich는 유지 (새 책 수집 계속, egress 미미)
- **수동 빌드 필요 시**: `build-index.yml` (workflow_dispatch) 사용
- **결과: 일 970MB → 0MB egress 절감**
- 새 책이 추천 인덱스에 반영 안 되지만, 현재 유저가 없으므로 영향 없음

### 6.2 단기

- Phase 3 증분 빌드 완료 후 `build-and-recompute` 재활성화
- Supabase 대시보드에서 실제 egress 소스 확인 (build_index vs API 요청 비율)

### 6.3 중기

- Pro plan 전환 ($25/mo, egress 250GB) — 서비스 성장 시

## 7. 구현 순서

### Phase 0: 즉시 (4/15 전, 코드 변경 없음)
1. daily-pipeline.yml에서 `build-and-recompute` job 비활성화 (수동 트리거만)
2. 수동 build-index 실행도 당분간 중단
3. **이것만으로 egress 97% 절감** (일 970MB → 0MB)

### Phase 1: 서빙 최적화
1. `build_index.py`에 prestacked + desc_matrix + agg_reason 빌드 추가
2. 새 `v4-prestacked` 포맷으로 index.pkl 생성
3. `loader.py` 업데이트 (v4 로드 + backward compat)
4. `stage1_hybrid()` + `batch_score_prestacked()` 구현
5. `/recommend` API에 two-stage 적용

### Phase 2: 캐싱 + 비동기 (Phase 1 직후)
1. `recommendation_cache` 테이블 생성 (migration)
2. `/recommend`에 캐시 확인 로직 추가
3. `/feedback`에 비동기 재계산 트리거 추가
4. 동시성 보호 (computing flag)

### Phase 3: 증분 빌드 + egress 절감
1. `books`, `book_v3_vectors`, `book_love_reasons`에 `updated_at` 트리거 추가
2. `build_index.py`에 `--incremental` 모드 추가
3. daily-pipeline에 증분 모드 적용
4. 주 1회 전체 재구축 스케줄

### Phase 4: 프로덕션 검증
1. Render 서버에서 레이턴시 실측
2. 후보 수 조정 (Render 성능에 맞춰)
3. egress 모니터링

## 8. 알려진 제약 (검증 완료)

아래 항목은 잠재적 문제로 식별 후 검증하여, 현재 규모에서는 문제 아님을 확인한 것.

### 8.1 캐시 hit에도 Supabase 쿼리 2회

`/recommend` 요청 시 캐시 hit이어도 user_books + recommendation_cache SELECT 2회 발생.
- **왜 피할 수 없나**: input_hash 계산에 user_books 최신 상태가 필요
- **영향**: Supabase Singapore ↔ Render Singapore ~10ms × 2 = ~20ms 추가
- **200ms 목표에 영향?**: 캐시 hit 시 응답이 ~20~50ms이므로 여유 충분
- **언제 문제 되나**: 유저 100+ 동시 요청 시 → Redis 도입으로 해결 (Layer 2)

### 8.2 인덱스 재빌드 ↔ 서버 반영 갭

build_index → git push → Render 재배포 (~2~5분 소요). 이 사이 서버는 이전 인덱스 사용.
- **자동 해결**: 재배포 완료 시 서버 restart → `built_at` 갱신 → 모든 캐시 stale → 재계산
- **갭 동안**: 이전 추천 반환 (새 책 미포함). 2~5분이므로 수용 가능.

### 8.3 삭제된 책의 유령 추천

DB에서 책이 삭제되어도 index.pkl에 남아 추천될 수 있음.
- **현재 발생 빈도**: 파이프라인에 DELETE 로직 없음. 수동 삭제 시에만 발생.
- **방어**: `/recommend` 응답 시 `books_meta`에 없는 book_id 필터링 추가 (구현 시 적용)
- **근본 해결**: 전체 재구축 (주 1회) 시 자동 정리

### 8.4 /similar 엔드포인트

`/similar/{book_id}`와 `/similar/union`은 `desc_matrix @ query_vec` 연산.
- 현재 ~1ms, 5만권에서도 ~10ms
- Two-stage 최적화 대상 아님 (이미 충분히 빠름)

## 9. 범위 외 (변경 없음)

- FAISS / ANN 전환: 10만권+ 시점에서 검토
- Warm stage (Hybrid CF): Stage 1에 CF 신호 추가 필요 — 별도 설계
- cap_dynamic 구현: 별도 과제 (이 spec은 서빙 인프라)
- H10_no_l1 가중치 코드 반영: 별도 과제 (config.py 변경)
- Render 유료 전환: 5천권 도달 시 결정
- Git LFS 탈피: 1만권 도달 시 결정

## 9. 출시 후 결정 항목 (Layer 2)

| 항목 | 초기값 | 조정 시점 |
|------|--------|----------|
| Stage 1 후보 수 | 700 | Render 실측 후 |
| 캐시 top-N 저장 수 | 50 | 유저 패턴 관찰 후 |
| computing flag timeout | 30초 | 동시성 이슈 발생 시 |
| 증분 vs 전체 빌드 주기 | 매일 증분 + 주 1회 전체 | 인덱스 drift 모니터링 후 |
| Stage 1 가중치 (desc 3.0, reason 2.0, fb 2.0) | 현재값 | 알고리즘 변경 시 |
