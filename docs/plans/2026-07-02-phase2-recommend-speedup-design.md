# Phase 2 설계 — 추천 계산 자체 단축 + 품질 전수 재검증

> 2026-07-02. Phase 1(PR#33·#34)이 계산을 쓰기 시점으로 옮겨 읽기를 빠르게 했지만,
> 계산 자체(prod 8~17s)는 그대로다. 이 문서는 계산을 sub-2s로 줄이되
> **추천 품질(취향)을 단 하나도 바꾸지 않는** 경로와, 그것을 증명하는 재검증 체계를 설계한다.

---

## 0. 핵심가치 정렬 — 왜 이 순서인가

| 근거 | 내용 |
|------|------|
| 핵심가치 3 (PRODUCT_PLAN.md §2) | **맞춤 추천** — "베스트셀러가 아닌, 나의 취향에 맞는 책 발견" |
| 유저 저니의 보상 순간 | 좋아요/평가 → **직후 추천이 바뀌는 것을 확인**하는 순간이 피드백 루프의 보상. Phase 1 이후에도 이 순간엔 재계산 대기(skeleton 8~17s)가 남아 있다 |
| Eden 원칙 | 취향 붕괴 경계 · band-aid 금지 · 근본원인 · 실검증 루프 · 품질 무손상 우선(핸드오프 학습 #3) |

따라서 설계 원칙: **① 스코어링 수식·가중치·후보 선정 의미 무변경 ② 최적화는 수학적 동치
변환만 ③ 그럼에도 float 연산 순서가 바뀌므로 전수 재검증으로 "동일함"을 증명 ④ 결과가
바뀌는 레버(ANN, 후보 수 변경)는 별도 트랙으로 격리.**

---

## 1. 코드리뷰 결론 — 진짜 병목은 업캐스트가 아니라 "반복 호출 구조"

recompute 전체 경로: `cache.recompute_recommendations` (engine/cache.py:173)
→ DB read ×2 + `ensure_*` 임베딩 → `stage1_hybrid` → `batch_score_prestacked` → meta+save.

### 1-1. Stage 2가 최대 용의자 — 후보×쿼리책 이중 Python 루프 (engine/twostage.py:174-300)

- 후보 150개(외부 루프) × 쿼리책 G+B개(내부 루프)마다 **같은 쿼리 reason 배열을
  f16→f32 재업캐스트** (twostage.py:198, 227). 150×~25 = **~3,750회 중복 astype**
  (~25×2000×4B = 회당 ~200KB 복사) + 회당 numpy 호출 오버헤드.
- 아이러니: v3 폴백 경로(`scorer.recommend_scores_two_stage`, engine/scorer.py:152-180)에는
  이미 **concat + `np.maximum.reduceat` 완전 벡터화 패턴**이 있고 "full 대비 top-10 일치
  10/10, max|Δ|<0.001"로 검증까지 됐는데, 정작 라이브 v4 stage2는 이 패턴을 안 쓴다.

### 1-2. Stage 1 — 항별 matvec 루프는 선형이라 접힌다 (engine/twostage.py:96-117)

- 블록마다 `pb_desc_terms`(2G+B+F개 항)와 `fb_terms`를 **항마다 별도 matvec**으로 돈다.
- 전부 선형결합이므로 수학적으로 동치: `Σ coef·(dm @ q) = dm @ (Σ coef·q)`.
  → 사전에 항들을 **단일 결합 쿼리벡터**로 접으면 블록당 GEMM 2회(max용 sq_desc/sq_reason)
  + matvec 3회로 고정. 메모리 전략(STAGE1_CHUNK 블록 업캐스트)은 그대로 유지.
- 핸드오프의 "전수 f16→f32 업캐스트 병목" 진단은 절반만 맞다: 업캐스트 자체는 블록당
  1회(전체 ~수백ms 고정비)이고, 진짜 비용은 업캐스트된 블록 위를 **항 수만큼 반복해서
  도는 것**(메모리 트래픽 ×항수)이다.

### 1-3. 스코어링 밖 I/O 꼬리 (계측으로 확정 필요)

- `ensure_feedback_embedded`/`ensure_books_embedded` (engine/user_embed.py) — OpenAI 호출은
  누락분에만 발생하지만, **평가 직후 첫 recompute가 바로 그 경우**(~0.5-1s).
- Supabase REST 왕복 3~4회 (user_books 2회 + extra_query + cache upsert).
- 현재 recompute에 스테이지별 타이밍 로그가 **전혀 없어** prod 8~17s의 분해를 모른다.

### 1-4. ANN(hnswlib) / int8 — 이번엔 도입하지 않는 근거

| 후보 | 판정 | 근거 |
|------|------|------|
| hnswlib ANN | **보류** (N≥50k 레버) | ① stage1은 단일 쿼리 최근접이웃이 아니라 **하이브리드 점수**(max-over-good + 선형항 + 전코퍼스 min-max 정규화, twostage.py:105-131) — ANN로 대체하면 후보 선정 의미 자체가 바뀜 = 취향 리스크. ② 내부 f32 저장 +76MB — prod 335/512MB에서 위험. ③ index-direct.yml 빌드 아티팩트 추가 = 파이프라인 복잡도. ④ N=9,483은 exact GEMM 2패스로 sub-1s 가능한 규모 — ANN은 문제의 근본원인(반복 호출 구조)이 아닌 증상 우회 |
| int8 양자화 | **기각** | numpy에 int8 GEMM 커널이 없어 정수 승격으로 **오히려 느려짐**. "f16 연산 금지"(project_perf_freetier)와 같은 계열의 함정. int8 SIMD는 전용 커널(예: hnswlib SQ8)이 있어야 의미 — ANN 재검토 시점에 함께 |

---

## 2. 설계 — PR 2개 + 후속 트랙

### PR-A: 계측 (행동·품질 무변경, 즉시)

측정이 가설보다 먼저다. prod 8~17s의 분해(I/O vs stage1 vs stage2)를 확정한다.

- `recompute_recommendations`에 스테이지별 `time.perf_counter()` 구간 측정 →
  완료 시 한 줄 로그: `recompute u=<uid> total=Xs db1=… embed=… db2=… extra=… s1=… s2=… save=…`
- `/health`에 `last_recompute_timings` 노출(선택) — Render 로그 안 뒤져도 되게.
- 배포(자동, ~8.5분) → throwaway 유저(ref_prod_e2e_throwaway)로 `POST /recompute` →
  **prod 스테이지별 baseline 확보.** 이 수치가 PR-B의 "전/후" 비교 기준.

### PR-B: 점수보존 벡터화 (본 작업)

**B-1. Stage 1 항 접기** (engine/twostage.py `stage1_hybrid`)
- 루프 밖에서 결합 쿼리벡터 3개 사전 계산: `q_fb = Σ sign·emb`,
  `q_pb_desc = Σ coef·q(desc항)`, `q_pb_agg = Σ coef·q(agg항)`.
- 블록 루프 내부: `sq_desc/sq_reason` GEMM 2회(불변) + matvec 3회. 정규화·읽은책 제외·
  top_n 선별 로직 무변경.
- 동치성: 수식 동일, float 합산 순서만 변경(f32 라운딩 ~1e-6). → 후보 150 집합 일치로 검증.

**B-2. Stage 2 벡터화** (`batch_score_prestacked` 대체 신규 함수, 기존 함수는 검증 기준선으로 유지)
- scorer.py:152-180의 검증된 패턴 적용: 후보 reasons를 **한 번** concat(f32) + 세그먼트
  오프셋 → 쿼리책별 GEMM 1회 + `np.maximum.reduceat` → (nq, C) → mean.
- 쿼리 reason 업캐스트를 후보 루프 밖으로(책당 1회). fb_sim도 `CR @ emb` + reduceat.
- desc/fb_desc/l1·l2(가중치 0 분기)/source_tier positive-part 페널티(twostage.py:295-300)/
  extra_query 주입/빈 reason/인덱스 밖 후보 제외 — **전부 기존 의미 그대로.**
- 메모리 transient: 150 후보 × 평균 ~10 reasons × 2000 × f32 ≈ **+12~15MB**
  (기존 검증된 안전선 54MB 이내, config.py:40-43). `INLINE_COMPUTE_CONCURRENCY=1` 가드 유지.
- pkl 포맷·빌드 파이프라인(index-direct.yml) **무변경.** (선택 최적화: 시작 시 prestacked
  dict에서 글로벌 concat 행렬+오프셋을 파생해 dict 오버헤드 제거 — 로드타임 1회, 부담 없음)

**예상 효과**: stage1 메모리 트래픽 ~항수배→2패스, stage2 numpy 호출 ~수천→~30회.
로컬 실측으로 확인하되, 보수적으로도 스코어링 sub-1s 예상. I/O 꼬리(~1s)는 계측 후 판단.

### 후속 트랙 (이번 범위 밖, 명시적 보류)

1. **I/O 꼬리 축소** — PR-A 계측에서 크게 나오면: 2차 user_books read 제거 검토
   (`ensure_feedback_embedded`가 행을 in-place 갱신하므로 재read 없이 hash 계산 가능,
   cache.py:249-253), upsert 병합 등. 별도 소PR.
2. **STAGE1_TOP_N 150→700 복원** — 벤치마크(benchmark_final.py §3)상 recall min 개선
   레버. stage2가 싸지고 메모리 안전해지면 가능하지만 **결과가 바뀌는 품질 변경**이므로
   별도 PR + Layer 1 풀 배터리 + Eden 승인 게이트.
3. **ANN/int8** — N≥50k 또는 벡터화 후에도 스코어링 >2s일 때만. 도입 시 후보 선정
   의미가 바뀌므로 recall + 다면성 + niche 가드 전수 평가 필수.

---

## 3. 품질 전수 재검증 체계 (Eden 최우선 — "취향 붕괴" 방지 증명)

"스코어링 무변경"을 주장이 아니라 **측정으로 증명**한다. 4개 레이어, 아래 순서대로 게이트.

### Layer 0 — 유닛 동등성 (pytest, CI)
- `tests/test_twostage.py` 확장: **old vs new 직접 비교** 테스트 —
  good/bad/fb/extra_query/neutral/빈 reasons/인덱스 밖 후보 전 조합 합성 픽스처에서
  `max|Δ| < 1e-4` + 순위 완전 일치. 기존 `_score_one` 대조 테스트(:109)·chunk 불변
  테스트(:66)·tier 페널티 테스트(:171) 전부 신규 경로로도 통과.

### Layer 1 — 실인덱스 오프라인 전수 (로컬 index.pkl 245MB, 현행 v4)
스크립트 신규 `recommendation-server/scripts/verify_equivalence.py` (benchmark_final.py의
유저 생성기 + scripts/test_persona_recommendations.py 페르소나 하네스 재사용):
- **유저 셋**: 18 페르소나 + 랜덤 50명 + 장르클러스터 30명 (기존 검증과 동일 구성).
- **동등성**: old vs new — stage1 후보 150 집합 overlap(기대 150/150, 148 미만이면 원인
  규명 전 진행 금지), 최종 top-20 순위 동일, 점수 `max|Δ|` 리포트.
- **품질 속성 유지 확인**(전체 DB 검증 원칙): 장르 다면성(top-20 클러스터 커버리지 전후
  동일), 싫어요 회피, source_tier 페널티 반영, 읽은 책 제외, dedup(engine/dedup.py) 동작.

### Layer 2 — 레이턴시·메모리 (Render free 모사)
- Docker `--cpus=0.5 --memory=512m`에서 스테이지별 p50/p95 전/후 측정 (유저 프로필
  6/10/20/40 likes). **합격선: 스코어링(s1+s2) sub-1s, peak RSS < 400MB.**
- 40+ likes 대형 유저에서 stage2 transient 실측(+15MB 예상 확인).

### Layer 3 — prod 검증 (배포 후)
- PR 머지 → 자동배포 → **behavioral E2E** (throwaway 실 JWT, ref_prod_e2e_throwaway):
  `POST /recompute` → recommendation_cache polling으로 end-to-end 실측, PR-A baseline과
  스테이지별 비교. `/recommend`·`/home`·`/similar` 응답 무변화 확인. `/health` memory_mb.
- **Eden 실계정 스냅숏 비교**: 배포 **전** recommendation_cache의 top-20 SELECT 저장 →
  배포 후 재계산 → top-20 비교. 동일해야 정상(취향 무손상의 최종 실물 증거).
- 폰 체감: 평가 → skeleton → 추천 갱신까지 체감 시간 (Eden 확인 항목에 추가).

### 합격 기준 요약
| 항목 | 기준 |
|------|------|
| 동등성 | 98명 전원 top-20 동일, 후보 overlap ≥ 149/150 |
| 레이턴시 | prod 재계산 end-to-end sub-2s (스코어링 sub-1s) |
| 메모리 | peak RSS < 400MB, /health memory_mb 배포 전후 동등 |
| 회귀 | pytest 164+신규 전부 green, 앱 무변경(서버만) |

---

## 4. 실행 순서

1. **PR-A** `feat/recompute-timings` — 계측 (~30분) → 머지·배포 → prod baseline 실측 기록.
2. **PR-B** `perf/twostage-vectorize` — B-1 + B-2 + Layer 0 테스트 + verify_equivalence.py.
   로컬에서 Layer 0→1→2 통과 후 PR. (TDD: 동등성 테스트를 먼저 old 기준으로 작성)
3. 머지·배포 → Layer 3 (throwaway E2E + Eden 계정 스냅숏 + 실측 비교).
4. 문서/메모리 갱신: NEXT_SESSION.md, memory project_status, 이 문서에 실측 결과 追記.
5. 후속 트랙(§2)은 결과 보고 후 Eden 판단.

### 주요 파일
- 수정: `recommendation-server/engine/twostage.py`(stage1 접기 + stage2 신규),
  `engine/cache.py`(계측), `engine/recommend_core.py`(신규 stage2 배선)
- 신규: `recommendation-server/scripts/verify_equivalence.py`, `tests/test_twostage.py` 확장
- 무변경: 스코어링 가중치(config.py), pkl 포맷, index-direct.yml, 앱(lib/), API 시그니처

### 리스크·롤백
- 서버 단독 변경 + auto-deploy → 문제 시 `git revert` 머지로 ~8.5분 내 원복.
- computing stuck 가드(180s)·stale-write 가드·Semaphore 등 기존 견고화 로직 무접촉.
- gh 계정 `hyhuh0910` 확인 후 push (반복 이슈).
