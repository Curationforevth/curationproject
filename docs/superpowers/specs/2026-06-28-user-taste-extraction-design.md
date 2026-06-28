# 설계 — 유저가 고른 어떤 책에서든 취향 추출 (통합 취향 모델)

> 2026-06-28 · 작성: PM-agent (Eden 위임 판단) · **v2 (4-에이전트 적대적 리뷰 반영)**
> 관련: `docs/backlog-2026-06-26-home-and-recs.md` (NEXT 항목), PRODUCT_PLAN §2·§4·§6

## 1. 목적 / 핵심가치 정렬 (정직한 범위)

| 기준 (PRODUCT_PLAN) | 이 설계가 달성하는 것 / 못 하는 것 |
|---|---|
| 비전 "베스트셀러 아닌 *나의* 취향" | **취향 *벡터*는 유저 책·피드백에서 정확히 추출.** 단 추천 *대상 풀*은 이번 범위에서 정적 인덱스 유지 → "베스트셀러 아닌"의 완전 달성은 **후보풀 후속(§3)에 게이트됨**(아래 정직성 노트). |
| 핵심가치 #3 맞춤 추천 | 유저 책이 인덱스에 없어도 취향에 반영 → 빈/빈약 추천 해소 |
| 핵심가치 #2 취향 발견 | 감정태그+한줄감상을 라이브 취향 *매칭*에 사용. **유저에게 "왜 좋았는지"를 보여주는 surfacing은 Phase 2**(이번엔 매칭 신호로만 — 과장 금지) |
| §6 취향 모델 = *피드백* 기반 | 피드백 임베딩을 책 맥락 유지한 채 같은 세션 반영 |
| User journey 4-2/4-3 | 책 추가/좋아요/피드백 → 같은 세션에 내 취향 반영된 추천 (tier2 이상; 그 미만은 §8 콜드스타트) |

> **정직성 노트(리뷰 BLOCKER-for-value 반영):** 이 MVP는 "취향을 정확히 뽑는다"까지다. 뽑은 취향이 *베스트셀러가 아닌* 책으로 이어지려면 후보풀에 니치 책이 있어야 하는데, 후보 편입은 Eden Q1에서 명시적으로 deferred("같은 세션"만, "둘 다" 아님)했다. 따라서 "베스트셀러 아닌"의 **완전 실현은 후보풀 후속(§3 #1)이 짝**이며, 본 작업 단독으로는 미완. 성공기준(§8)은 이를 과장하지 않는다.

## 2. 문제 (코드로 검증)

유저가 카카오 검색으로 추가/좋아요한 책이 추천에 거의 반영되지 않는다. 원인 3가지:

1. **유저 책이 임베딩 안 됨.** 앱은 카카오 `contents`를 `books.description`에 저장하지만, 임베딩 파이프라인은 `rich_description ≥ 200자`만 처리([generate_book_v3_vectors.py:38-49](../../scripts/generate_book_v3_vectors.py)). 카카오 책은 `rich_description`이 비어 SKIP → `book_v3_vectors`에 없음.

2. **임베딩돼도 서빙이 정적 인덱스에서만 취향을 읽음.**
   - [twostage.py:33-35](../../recommendation-server/engine/twostage.py) — Stage1이 `bid_to_idx`로 인덱스 행 매핑, 인덱스 밖 책 드롭. 좋아요 책이 *전부* 인덱스 밖이면 `if not good_desc_indices: return []` → 후보 0.
   - [twostage.py:115-116](../../recommendation-server/engine/twostage.py) — Stage2가 `index.get_book(bid)` → None이면 드롭.
   - DB(`book_v3_vectors`)에서 좋아요 책 벡터를 읽는 경로 없음.

3. **수집 중인 피드백 신호가 라이브 추천에서 버려짐** (제품 §6 핵심인데 미반영):
   - **`emotion_tags`는 라이브 서빙에서 전혀 안 쓰임.** recompute SELECT([cache.py:178-180](../../recommendation-server/engine/cache.py))는 `book_id, rating, feedback_embedding`만 읽음. `emotion_tags`는 [taste_recomputer.py:162](../../scripts/taste_recomputer.py)(서빙 미사용)·실험 코드에만 등장.
   - **`feedback_embedding`은 `review_text`만 임베딩**([feedback.py:40-42](../../recommendation-server/api/feedback.py)), 태그 미포함. 앱은 POST /feedback 미호출(Supabase 직접쓰기) → `feedback_embedding`은 하루 1회 backfill로만 채워짐(같은 세션 X). backfill도 review만([backfill_feedback_embedding.py](../../scripts/backfill_feedback_embedding.py)).
   - ✅ 단 `feedback_embedding` *자체*는 라이브 twostage에서 실사용됨([twostage.py:43-47,63-67,156-161,218-224](../../recommendation-server/engine/twostage.py)) — 과거 "미사용" 주장은 거짓. 문제는 "review만/배치만"이지 "안 쓴다"가 아님.

**관측**(handoff #7): Eden 좋아요 7권 중 인덱스 1권, taste_vectors=0, 추천 빈약. 앱 실측(리뷰): **온보딩 그리드 없음 — 첫날부터 카카오 검색**([register_flow_screen.dart](../../app/lib/features/register/screens/register_flow_screen.dart))으로만 책 진입 → 인덱스 밖 책이 기본값. 추천은 pull-to-refresh로만 갱신([home_screen.dart:107-120](../../app/lib/features/home/screens/home_screen.dart)).

## 3. 결정 (스코프)

**IN (이번 작업): "통합 취향 모델"**
- C1. 유저 책 임베더 — 가용 텍스트로 embed-once → `book_v3_vectors`
- C2. 라이브 취향 보강 — DB 벡터를 *쿼리* 벡터로 주입(후보풀은 정적 인덱스 유지)
- C3. 피드백 신호 라이브 반영 — 감정태그+한줄감상 → `feedback_embedding`, 같은 세션, 책 맥락 유지
- C4. 트리거 배선 + **캐시 코히런스**(리뷰 BLOCKER 반영)

**OUT (명시적 후속, 우선순위):**
1. **후보풀 커버리지** (유저/타유저 니치 책을 정적 인덱스 후보로 편입 + 대중·니치 한국서 보강). **"베스트셀러 아닌" 가치의 짝 → 다음 최우선.** 대량 egress + OpenAI + 수동 배포 게이트([[feedback_supabase_egress]]).
2. **취향 발견 surfacing** — 매칭된 reason으로 "이 책의 ~한 점이 취향에 맞아요" 표시(Phase 2; 데이터는 이미 계산됨 — 저비용 후속 후보).
3. **유저 책 LLM reason 추출 / 공유 book_love_reasons(C 레버)** — Phase 2.
4. **`input_hash` 콘텐츠 staleness** — 리뷰 *수정* 시 `has_fb`(0/1) 불변으로 재계산 누락(기존 버그, 기록만).

## 4. 아키텍처

원칙: **앱 변경 0** (recompute가 앱이 이미 쓴 `rating/emotion_tags/review_text`를 읽음). **OpenAI는 백그라운드 recompute에서만**(요청/인라인 경로 보호). **embed-once 축적**([[feedback_accumulate_not_realtime_api]]).

```
유저: 책 추가/좋아요/피드백 (앱 → Supabase 직접쓰기: books, user_books)
            │  (다음 홈/새로고침: GET /recommend|/home)
            ▼
캐시미스 → ① inline(이미 임베딩된 것으로 즉시 best-effort)
         └ ② "미임베딩 좋아요책 OR 미임베딩 피드백 존재" 이면 → 항상 BackgroundTask 큐잉
                                                              (inline 성공해도)
BackgroundTask: recompute_recommendations
   1) ensure_feedback_embedded(태그+리뷰)   ← C3 (best-effort, per-book try/except)
   2) ensure_book_embedded(좋/싫 책)         ← C1 (best-effort, 벡터 반환)
   3) user_books 재read → input_hash 재계산  ← 코히런스 (§4.5)
   4) 좋/싫 책 벡터 resolve: 인덱스 → 없으면 DB/2)의 반환  ← C2
   5) stage1_hybrid + batch_score_prestacked (augmented)
   6) save_cache_if_current (post-embedding hash)
            │
            ▼  (다음 호출: 캐시 히트 → 내 책·피드백 반영)
```

### C1. 유저 책 임베더 — `recommendation-server/engine/user_embed.py`
- `ensure_books_embedded(book_ids) -> dict[bid → desc_vec(f32, 2000)]`. OpenAI 호출은 [feedback.py:17 `_embed_text`](../../recommendation-server/api/feedback.py) 패턴 재사용(text-embedding-3-large, 2000D, [config.py](../../recommendation-server/config.py) `EMBEDDING_DIMENSIONS=2000`).
- 책별 (best-effort, **per-book try/except** — 일부 실패해도 나머지 진행):
  1. 이미 `book_v3_vectors`에 있으면 그 벡터 반환 (embed-once, OpenAI 0회).
  2. 없으면 **가용 최선 텍스트**: `rich_description`(≥200자) → `description`(카카오 contents) → `title + author + genre`(최후, 저신뢰).
  3. 임베딩 → `book_v3_vectors` upsert(**컬럼 명시**: `book_id, desc_embedding, source_text, l1_genre_id, l2_genre_id, provisional`). genre 있으면 `genre_embeddings`에서 l1/l2 id 조회, 없으면 NULL.
  4. rich 아닌 텍스트면 `provisional=TRUE`(후속 보강 대상). rich면 FALSE.
  5. 임베딩한(또는 기존) desc 벡터를 반환 → C2가 DB 재조회 없이 사용.
- **품질 주의(리뷰 MAJOR 5):** 카카오 `contents`는 보통 한 문단(아라딘 142자 문제와 다름) → 대체로 적합. 단 `title+author+genre` 최후수단은 얕아 desc 신호 약함 → query-side 한정(후보 corpus 오염 X)이고 §7에서 **얕은 vs rich 추천 품질 비교 측정** 필수. reason 미추출 → reason r_sim 기여 0(정상). *단 피드백 있으면 fb_sim은 별도 기여(§C2 노트).*

### C2. 라이브 취향 보강 — 스코어러 확장 (정밀 명세, 리뷰 MAJOR 3·4)
- `stage1_hybrid`/`batch_score_prestacked`에 `extra_query: dict[bid → BookVectors]` 인자 추가.
- recompute가 좋/싫 책 벡터 resolve: 정적 인덱스에 있으면 그대로, 없으면 C1 반환/`book_v3_vectors`로 합성:
  `BookVectors(desc=to_np(desc_emb)[f32,(2000,)], l1=np.zeros(2000,f32), l2=np.zeros(2000,f32), reasons=[])`
  ([index.py:8-13](../../recommendation-server/engine/index.py) BookVectors는 l1/l2 비-Optional ndarray → **None 금지, zero 벡터 필수**. `W_L1=W_L2=0`([config.py](../../recommendation-server/config.py))이고 `if w_l1!=0` 가드라 zero 안전).
- **`stage1_hybrid` 보강 (양쪽 다):**
  - `good_descs = vstack([dm[idx_hits], *extra_good_descs])`, `good_aggs = vstack([am[idx_hits], *zeros])` — sq_desc/sq_reason의 쿼리 집합에 주입책 포함(shape 정합 위해 agg는 zero row).
  - **per-book `pb_scores` 루프**([twostage.py:52-62](../../recommendation-server/engine/twostage.py))에 주입책 **desc 항만** 추가: 좋아요 `pb_scores += 3.0*(dm @ extra_desc)`, 싫어요 `-= 1.5*(dm @ extra_desc)`. (안 하면 desc 신호 절반 — `combined=sq_norm+pb_norm`이라.)
  - ⚠️ **fb 항은 추가 금지(이중계산 방지, 리뷰 R2):** `sq_fb`([twostage.py:43-47](../../recommendation-server/engine/twostage.py))·pb fb([twostage.py:63-67](../../recommendation-server/engine/twostage.py))·`fb_desc`([twostage.py:218-224](../../recommendation-server/engine/twostage.py)) 루프는 `fb_data.items()`를 **`bid_to_idx` 가드 없이** 돈다 → 인덱스 밖 주입책의 fb 신호는 *이미 full-weight로* 반영됨. 주입책용 fb 항을 또 더하면 2배 가중. desc 항만 주입한다.
  - **early-exit 확장**([twostage.py:34](../../recommendation-server/engine/twostage.py)): `if not good_desc_indices and not extra_good: return []` — 인덱스 hit가 0이어도 주입 good이 있으면 진행(=Eden 케이스의 핵심).
- **`batch_score_prestacked` 보강:** `good_books = {bid: index.get_book(bid) or extra_query.get(bid)}` 후 None 필터. desc_score는 주입책 desc 포함([twostage.py:198-199](../../recommendation-server/engine/twostage.py)).
- **dtype:** 인덱스는 f16, 주입책은 f32(`to_np`) — 스코어링이 `.astype(f32)` 업캐스트하므로 무해. **f16 `desc_matrix_f16`에 직접 넣지 말 것**(stage1은 vstack한 별도 f32 배열로).
- **v3 폴백 미보강(리뷰 R2 NEW#3):** prod 인덱스는 v4-prestacked(`/health` version 확인)라 `app_state.prestacked_reasons`가 항상 non-None → recompute는 `stage1_hybrid`+`batch_score_prestacked` 경로만 탐. `prestacked is None`일 때의 `recommend_scores_two_stage`([scorer.py](../../recommendation-server/engine/scorer.py))는 **의도적으로 보강 안 함**(prod 미도달). recompute 시작에 `prestacked is not None` assert/경고 로그 추가해 회귀 가시화.
- **read_ids 제외:** 주입 좋아요책은 `bid_to_idx`에 없어 [twostage.py:82-85](../../recommendation-server/engine/twostage.py) 제외 루프를 안 타지만, *애초에 후보 `dm`에 없어* 추천에 안 나옴 → 무해(리뷰 확인).
- **centroid 금지(P5/취향=스펙트럼):** 라이브 스코어러의 per-book max-sim([twostage.py:40](../../recommendation-server/engine/twostage.py) `(dm @ good_descs.T).max(axis=1)`)을 그대로 쓴다. `experiment_confidence.py:266`의 `np.average` centroid는 **차용 금지**(SF+에세이 같은 다축 취향을 평균으로 뭉갬).
- **후보풀(추천 대상)은 정적 인덱스 그대로.** 유저 책은 "취향 소스"로만(후보 편입은 §3 후속).

### C3. 피드백 신호 라이브 반영 — `ensure_feedback_embedded`
- recompute SELECT를 `book_id, rating, feedback_embedding` → **`+ emotion_tags, review_text`** 로 확장([cache.py:178](../../recommendation-server/engine/cache.py)).
- 좋/싫 책 중 (`emotion_tags` 또는 `review_text` 있음) AND `feedback_embedding` 없음이면 (best-effort):
  - **임베딩 입력 문자열(정본, 리뷰 MINOR — 절대 truncate 금지):**
    `f"태그: {', '.join(emotion_tags)}\n{review_text or ''}".strip()`
    (이모지·`리뷰:` 라벨·`[:40]` 절단 **없음**. `experiment_confidence.py`의 `fmt_feedback`은 *디스플레이* 포매터라 차용 금지 — 원문 보존 [[feedback_reason_extraction]] P4.)
  - 임베딩 → `user_books.feedback_embedding` 갱신 → `fb_data[bid]`로 스코어링.
- **책 맥락 유지(P1)·축 독립(P3):** 피드백 임베딩은 `fb_data[bid]`로 *그 책에 묶여* 후보와 의미 매칭([twostage.py:43,63,140](../../recommendation-server/engine/twostage.py)). 태그+리뷰 결합은 **같은 축(피드백) 내 결합**이지, reason/desc/genre 같은 *서로 다른 축*을 합치는 게 아님 → [[feedback_vector_separation]] 위배 아님. (태그와 리뷰가 상충하는 경우는 MVP 허용 리스크로 명시.)
- **backfill 동기화:** [backfill_feedback_embedding.py](../../scripts/backfill_feedback_embedding.py)도 같은 입력 문자열(태그+리뷰)로 갱신 — 배치 경로가 태그를 누락하지 않도록.

### C4. 트리거 배선 (리뷰 BLOCKER 1 + R2 NEW#4)
- **SELECT 확장 필수:** 트리거 술어의 "피드백 텍스트 있는데 임베딩 없음" 절반을 계산하려면 캐시미스 경로의 user_books SELECT가 `emotion_tags, review_text`를 포함해야 함. 현재 [recommend.py:64-66](../../recommendation-server/api/recommend.py)·[home.py 동일 SELECT](../../recommendation-server/api/home.py)는 `book_id,rating,feedback_embedding`만 → **`+ emotion_tags, review_text`로 확장**(같은 행, 비용 무시).
- 캐시미스 시 `ub_res.data`로 **싼 술어** 계산 = "좋/싫 책 중 *인메모리 인덱스(`bid_to_idx`)에 없는* 책이 있다 OR (`emotion_tags`/`review_text` 있는데 `feedback_embedding` 없는 행이 있다)".
- 술어 참이면 → **inline 성공 여부와 무관하게 항상** `background_tasks.add_task(recompute_recommendations, ...)`. **두 사이트 모두**: [recommend.py:128-148 inline-성공 분기](../../recommendation-server/api/recommend.py) + [home.py inline-성공 분기](../../recommendation-server/api/home.py)(현재 둘 다 inline 성공 시 recompute 큐잉 없음).
- **그리고 술어 참이면 inline은 빈약 캐시 저장 skip**([recommend.py:141 `save_cache_if_current`](../../recommendation-server/api/recommend.py) 호출 안 함) — §4.5와 연동. 응답엔 best-effort를 주되 캐시 확정은 recompute에 위임.
- (인덱스 밖 책이 `book_v3_vectors`엔 이미 있을 수 있음 → 정밀 판정은 recompute 내부. 술어는 인메모리만 보고 false-negative 0 보장, 가끔 헛recompute 허용 — 이미 임베딩됐으면 recompute가 OpenAI 0회로 싸게 끝남.)
- inline = 즉시 best-effort(OpenAI 0). background = 누락 임베딩 + 보강 스코어링 → 다음 호출 반영.

### 4.5 캐시 코히런스 (리뷰 BLOCKER 2 — 신규)
충돌: inline이 빈약본을 hash H0로 저장 + recompute가 보강본을 같은 H0로 저장 → last-writer-wins로 빈약본 고정 위험. 또 C3가 `has_fb` 0→1로 hash를 바꿔 recompute의 H0 저장이 거부되는 루프.
- **수정 1:** inline은 위 "미임베딩" 술어가 참이면 **빈약 캐시를 저장하지 않는다**(`save_cache_if_current` skip) — 응답엔 best-effort를 주되 캐시 확정은 recompute에 위임. (술어 거짓=완전 임베딩 상태면 기존대로 inline이 저장.)
- **수정 2:** recompute는 **임베딩(C3→C1)을 먼저** 하고, 그 후 `user_books`를 **재read**해 `input_hash`를 *post-embedding 상태*로 재계산한 뒤 스코어링·저장. → 저장 hash가 live와 일치(거부/루프 해소).
- **수정 3 (R2 NEW#1):** recompute가 `computing=True` 플래그를 세울 때 **기존 `recommendations`를 비우지 말 것.** 현재 [cache.py:168-173](../../recommendation-server/engine/cache.py)은 `recommendations: []`로 덮어씀 → 수정 1(inline 저장 skip)과 겹치면 임베딩(수초) 동안 stale-serve 폴백([recommend.py:96](../../recommendation-server/api/recommend.py)은 `recommendations` 非빈 요구)이 무력화돼 그 시간 내내 inline만 반복. → computing 플래그는 `recommendations`를 건드리지 않게(컬럼만 update, 신규 유저는 빈 채로 insert) 변경. 기존 good 캐시가 recompute 중에도 보존됨.
- TDD로 "burst(인라인+recompute 동시)에서 보강본이 빈약본에 안 덮인다" + "computing 중 기존 recs 보존" 고정.

## 5. 데이터 / 마이그레이션
- `book_v3_vectors.provisional BOOLEAN DEFAULT FALSE` 추가(C1 후속 보강용). apply-migrations 자동([[feedback_no_direct_sql]]). **안전 확인:** `book_v3_vectors` 읽기는 전부 컬럼 명시(SELECT * 없음, 생성컬럼 없음 — `books.l1/l2` 버그와 무관). upsert도 컬럼 명시.
- 기존 `book_v3_vectors`(desc_embedding/source_text/l1l2/updated_at) 재사용. 신규 테이블 없음. `user_books` 스키마 변경 없음(읽기만).

## 6. 비용 (Eden 승인 게이트)
- OpenAI: text-embedding-3-large, **책당·피드백당 ~$0.0001, embed-once**. 수천 항목도 센트 단위.
- Supabase egress: recompute당 좋아요 책 몇 행 read = 미미. **인덱스 전체 재빌드 안 함** → 대량 egress 없음. (후보풀 후속이 큰 게이트.)
- 메모리: 주입 벡터 ~10권×4벡터×2000 f32 ≈ 0.3MB → 무료티어 헤드룸(382/512) 무해([[project_perf_freetier]]). 기존 stage1의 `dm/am.astype(f32)` 사본(~81MB)은 불변.

## 7. 검증 계획
DB 쓰기 경로 실검증 필수(CLAUDE.md / [[feedback_dryrun_limits]]).
- **단위 TDD:**
  - `ensure_books_embedded`: embed-once(중복 skip), 텍스트 폴백 순서, provisional 표시, per-book 실패 격리.
  - **stage1 augmentation: 인덱스 hit 0 + 주입 good만으로도 `[]` 아님**(early-exit 확장), 주입책이 `pb_scores`까지 바꿈, centroid 아님(다축 취향 보존).
  - `batch_score_prestacked`: 주입 BookVectors(zero l1/l2) 안전, desc_score 반영.
  - `ensure_feedback_embedded`: 태그+리뷰 **무절단** 입력 문자열, feedback_embedding 채움.
  - **코히런스:** burst에서 보강본이 빈약본에 안 덮임 / post-embedding hash 일치 / computing 중 기존 recs 보존(R2 NEW#1).
  - **fb 이중계산 방지(R2):** 인덱스 밖 + 피드백 보유 주입책의 fb 기여가 1배(주입책용 fb 항 추가 안 함 검증).
  - **reasonless+feedback:** reason 없는 주입책이 r_sim=0이지만 fb_sim(`cand_reasons @ fb_emb`, [twostage.py:156-161](../../recommendation-server/engine/twostage.py))으로는 기여함을 고정(§C1의 "reason 기여 0"은 r_sim 항 한정).
- **prod E2E**(throwaway, [[ref_prod_e2e_throwaway]]):
  - 인덱스 밖 책 6+권 좋아요(+일부 태그/리뷰) → /recommend 비어있지 않고 그 책·피드백 기반.
  - **얕은(title만) vs rich 임베딩 추천 품질 비교** — 얕은 책이 품질을 *저하*시키는지 측정(MAJOR 5).
  - **태그 유무 A/B** — 태그만 남긴(리뷰 X) 유저의 추천이 태그로 달라지는지.
  - **앱 실제 경로 관측성**: GET /recommend(=`recommendation_cache`, input_hash 키 — 시간버킷 아님)가 pull-to-refresh로 갱신됨 확인. (/home 큐레이션 섹션의 시간버킷 캐시는 #2 별건.)
  - memory_mb OOM 0.
- **회귀:** 기존 tier2(인덱스 내) 유저 추천 동치 + 캐시 정상.

## 8. 성공 기준 (정직)
- **인덱스에 없는 책만 좋아요한 tier2 유저도 빈/빈약이 아닌, 자기 책 기반 추천을 같은 세션에 받는다**(후보는 현 corpus 범위 — "베스트셀러 아닌" 완전 실현은 §3 #1 후속과 짝, 본 작업은 *취향 정확도*까지).
- 감정태그/한줄감상이 라이브 추천에 영향(태그 A/B로 확인).
- 다축 취향(SF+에세이) 보존(centroid 아님).
- 앱 변경 0, OpenAI 인라인/요청경로 0, 추가 비용 무시 가능, OOM 0.
- **콜드스타트 회귀 없음**(아래).

### 콜드스타트 / 저피드백 경계 (리뷰 MAJOR 6)
- 취향 추출은 **tier2(좋아요 6권)부터** 작동([twostage.py:24](../../recommendation-server/engine/twostage.py) `if not good_ids`, tier 게이트). 그 미만은 본 작업 범위 밖이며 **기존/별도 항목이 담당**:
  - tier<2: 홈의 큐레이션/트렌딩 + 빈 추천 placeholder가 journey를 메움(서버 [home.py](../../recommendation-server/api/home.py), 큐레이션 ≥3 보장은 backlog #2).
  - 진행 안내("맞춤추천까지 N권 더") = backlog #4(앱, 별건).
- **👍-only(태그/리뷰 0) 유저도 신호 0 아님**: C1/C2로 desc 기반 취향은 작동. 피드백 신호만 비는 것(리뷰의 "zero signal"은 피드백 축 한정 — 교정).
- **TDD:** "좋아요 5권 전부 인덱스 밖, 태그/리뷰 0" → tier1이라 personal 추천 게이트 + 큐레이션 폴백 노출(빈 화면 아님) 확인.

## 9. 미해결 / 리스크
- 인라인 best-effort는 첫 호출엔 새 책 미반영(백그라운드 1사이클 후) — 기존 패턴, 수용.
- 후보풀 미해결 시 추천 *대상*이 얇을 수 있음(취향 정확해도) → §3 #1 최우선 후속.
- `provisional` 재임베딩(후속 보강)은 범위 밖(스키마만 준비).
- 태그 vs 리뷰 상충 시 단일 피드백 벡터가 뭉개짐 — MVP 허용 리스크.
