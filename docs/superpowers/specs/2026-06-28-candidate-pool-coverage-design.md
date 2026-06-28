# 설계 — DB의 모든 책을 "가진 정보 최대로" 추천 가능하게 (후보풀 커버리지 근본구조)

> 2026-06-28 · 작성: PM-agent (Eden 위임 판단) · **v3 (2라운드 적대적 리뷰 반영)**
> Eden 지시(정본): **"인기가 있고 없고와는 상관없이 db에 있는 책을 골랐을 때 최대한 있는 정보를 바탕으로 유저에게 추천을 해낼 수 있는 구조."**
> 관련: `2026-06-28-user-taste-extraction-design.md`(§3 OUT #1 후속), `docs/NEXT_SESSION.md`(P1 #1·#2), PRODUCT_PLAN §1·§2(핵심가치 #3), `docs/PIPELINE.md`

## 1. 목적 / 핵심가치 정렬 (정직한 범위)

| 기준 (PRODUCT_PLAN) | 이 설계가 달성하는 것 / **못 하는 것** |
|---|---|
| 핵심가치 #3 "추천을 *해낼 수 있는*" | DB에 있는 책이면 (rich·인기 무관) 임베딩되어 후보풀·취향소스가 됨. rich 매칭이 없어도 빈약한 책이라도 추천 산출(graceful degradation) — Eden 지시 핵심 |
| User journey 4-2/4-3 | 유저가 카카오로 고른 책이 enrich 전이라도 후보·소스가 됨 |
| 비전 "베스트셀러 아닌" | **부분 달성.** tier1+ 의 /similar 레일과 tier2+ 개인추천 후보풀이 인기와 무관해짐. **단 tier0(좋아요 0~2) 콜드스타트가 보는 큐레이션/트렌딩은 본 설계 밖**(정직성 노트) |

> **정직성 노트(리뷰 M6 반영, R2 정밀화):** 콜드스타트 단계별 체감이 다르다 — **tier0(좋아요 0~2):** 홈의 큐레이션·트렌딩만 보이며 이는 `loan_count` 순위(`refresh_curation_cache_all`/`refresh_fallback_curation`, `book_v3_vectors` 아님)라 **본 설계가 안 건드림**(베스트셀러 그대로, §3 OUT). **tier1(좋아요 3~5):** `similar` 레일이 켜지고([tier.py](../../recommendation-server/engine/tier.py)) 이건 `book_v3_vectors` 기반이라 **본 설계가 커버**(thin seed 책도 빈 결과 안 됨 — 첫 실질 승리). **tier2+(좋아요 6~):** 개인추천 + thin 소스 반영. → "베스트셀러 아닌"의 *완전* 실현은 tier0 큐레이션 다양화(별도 후속)와 짝. 과장 금지.

### 1.1 신규 유저 여정 (헤드라인 딜리버러블 — R2 명시 요구)
앱 실측: 온보딩 그리드 없음, 첫날 카카오 검색으로만 책 진입. 신규 유저가 카카오로 **thin 책 6권**을 좋아요하는 경로:
- **좋아요 0~2 (tier0):** 트렌딩 + 큐레이션(loan_count) — 본 설계 영향 0(베스트셀러). *(향후 PRODUCT_PLAN §4-1 온보딩 그리드가 구현되면 likes를 선시드해 tier0 노출이 급감 — OUT #1 우선순위 가중 시 고려.)*
- **좋아요 3~5 (tier1):** `similar` 레일 점등. **본 설계로 thin seed 책도 비지 않는 유사 추천** — 콜드스타트 첫 실질 가치.
- **좋아요 6 (tier2):** 개인추천 점화. C1/C2가 6권 thin 책을 취향 소스 + thin 후보로 임베딩 → **비지 않고 그 책 기반의 개인추천**을 같은 세션에 받음.
- = Eden 지시("db 책을 골랐을 때 정보 최대로 추천을 *해낸다*")가 tier1(유사)·tier2(개인)에서 실제 달성.

## 2. 근본 원인 (코드로 검증)

임베딩 경로가 둘인데 **정책이 정반대**다:

| 경로 | 정책 | 결과 |
|---|---|---|
| **배치** `scripts/generate_book_v3_vectors.py` | `build_desc_source`([:43-46](../../scripts/generate_book_v3_vectors.py)): `rich_description < 200자` → `None` → **SKIP**. `fetch_target_books`([:84-99](../../scripts/generate_book_v3_vectors.py))는 `rich_description IS NOT NULL` 책만 조회 | 카카오 설명·title/author/genre만 있는 책은 **`book_v3_vectors`에 영원히 못 들어감 → 후보풀 제외** |
| **라이브(C1)** `recommendation-server/engine/user_embed.py` | `_pick_source_text`([:26-38](../../recommendation-server/engine/user_embed.py)): rich≥200 → 카카오 description → title+author+genre, 얕으면 `provisional=True` | "최대한 있는 정보로"를 이미 구현(유저 좋아요 책 한정, 라이브 recompute만). **이미 prod 라이브 → thin 행이 이미 존재**(§5 마이그레이션 backfill 근거) |

`build_index.py`([:189-192](../../recommendation-server/scripts/build_index.py))는 `book_v3_vectors` **전체**를 필터 없이 적재 → 후보풀 한계는 **배치 게이트**가 결정. 원래 게이트 의도는 "얕은 desc 벡터가 후보 오염". **Eden 지시는 이 트레이드오프를 커버리지 우선으로 뒤집되, 오염은 *배제*가 아니라 *경미한 차등 down-weight*로 관리.**

## 3. 결정 (스코프)

**IN:** C1 임베딩 소스 통일 · C2 후보 범위 확장 · C3 품질 등급(`source_tier`) · C4 down-weight 적용 · C5 upgrade-only + 재임베딩.

**OUT:** ① tier0 큐레이션/트렌딩 다양화 ② DB 밖 책 외부 대량 수집 ③ surfacing / 공유 book_love_reasons(Phase 2).

## 4. 아키텍처

### 4.0 배포 경계 + 페널티 운반 (리뷰 B2·B3·M1 해소)
- `recommendation-server/`만 Render 배포, `scripts/`는 GitHub Actions 전용 → **런타임 코드 공유 불가**. 폴백 정책·tier 문자열은 **테스트로 동기화**(§7).
- **페널티는 `VectorIndex` 인스턴스에 저장**(pkl `bundle["index"]`에 직렬화):
  - loader/main.py/app_state arity **변경 불필요**(B3). 번들 버전 `v4-prestacked` **불변**(B2 — 버전 크래시 없음). 신코드는 구 pkl을 `getattr(index, "_candidate_penalty", {})`로 폴백.
  - `/similar`도 `VectorIndex.similar_by_vector`가 같은 페널티 내부 적용(M1).

### 4.1 C1 — 임베딩 소스 정책 통일
- `generate_book_v3_vectors.py::build_desc_source` → 라이브 `_pick_source_text`와 **동일 로직**, 반환 `(text, source_tier)`:
  ```
  rich = clean_html(rich_description).strip(); if len(rich) >= 200: return (rich[:2000], "rich")
  desc = clean_html(description).strip();      if desc:            return (desc[:2000], "kakao_desc")
  m = " ".join([title, author, genre]).strip(); return (m[:2000], "minimal") if m else (None, None)
  ```
  - `_MIN_RICH = 200` 양쪽 동일(런타임 공유 불가 → 동등성 테스트).
- **M3 수정:** 라이브 `_pick_source_text`에도 `clean_html` 적용(현재 raw `len(rich)` → 배치와 게이트 불일치로 같은 책이 경로별 다른 임베딩/tier). recommendation-server에 경량 `clean_html`(`engine/utils.py`, `re.sub(r"<[^>]+>","",t)`, 평문 idempotent) **신설**(scripts/lib import 금지 — 배포 경계). 두 경로가 *정제 후* 길이로 게이트 = 진짜 동등.
- `provisional`은 `source_tier != "rich"`로 파생 유지.

### 4.2 C2 — 후보 범위 확장
- `fetch_target_books`: `rich_description IS NOT NULL` 필터 **제거**, SELECT에 **`author` 추가**.
  - **대표성 수정:** 이미 `book_v3_vectors`에 있는 book_id를 **쿼리/페이지 단계에서 제외 후 limit** 적용(현재는 전체 limit 자른 뒤 제외 → mode=small이 기존 책만 집어 신규 0 가능).
- embed-once 유지. upsert row에 **`source_tier`(+ `provisional` 파생) 추가**(현재 미기록 → 기본값 오저장).
- l1/l2: genre 있으면 `parse_genre`, 없으면 NULL(zero, W_L1=W_L2=0이라 무해).

### 4.3 C3 — 품질 등급 운반
- **config 상수:** `SOURCE_TIER_PENALTY = {"rich": 1.0, "kakao_desc": 0.95, "minimal": 0.85}`. **타이브레이크 수준의 경미한 페널티**(R2 niche-역전 방지 — §10). E2E로 튜닝. + `SIMILAR_MIN_TIER = "kakao_desc"`(minimal은 /similar 노출 제외, §4.4).
- **build 운반:** `build_index.py` v3 SELECT에 `source_tier` 추가([:190](../../recommendation-server/scripts/build_index.py)). 인덱스에 add하는 책(=skip 생존자, **M5**) 중 **non-rich만** `index._candidate_tier[bid] = tier`(sparse, 부재=rich). **tier 문자열을 1급으로 저장**(R3 blocker — 페널티 float만으론 /similar의 minimal 제외가 불가능; tier에서 페널티·제외집합을 파생). bid_order/desc_matrix와 같은 add 루프라 자동 정합.
- **VectorIndex(R2·R3):** `__init__`에 `self._candidate_tier: dict = {}`, `self._penalty_vec = None`, `self._exclude_similar: set = set()` **기본값 추가**(구 pkl은 `__init__` 미실행 → 모든 접근 `getattr(self,"_candidate_tier",{})`로 가드). `build_desc_matrix`에서 tier dict로부터 파생:
  - `_penalty_vec = np.array([SOURCE_TIER_PENALTY[getattr(self,"_candidate_tier",{}).get(bid,"rich")] for bid in _desc_bid_order], f32)`
  - `_exclude_similar = {bid for bid,t in getattr(self,"_candidate_tier",{}).items() if t == "minimal"}` (= SIMILAR_MIN_TIER 미만)
  lazy 가드([index.py:67](../../recommendation-server/engine/index.py))가 desc_matrix와 함께 둘 다 재생성.

### 4.4 C4 — down-weight 적용 (부호 안전, 리뷰 B1·M1)
**핵심:** `score *= 0.85`는 음수 점수(나쁜책·싫어요 항)를 0쪽으로 올려 랭크 *상승* 버그 → **positive-part 곱셈**:
- 규칙: `if pen != 1.0 and score > 0: score *= pen` (음수/0 미변경 → 절대 승격 안 함). 차원 없는 계수, 부호 안전, graceful degradation 동시 만족.
- **stage2** `batch_score_prestacked`([:253](../../recommendation-server/engine/twostage.py)): `tier = getattr(index,"_candidate_tier",{}).get(cid,"rich"); pen = SOURCE_TIER_PENALTY[tier]; if pen != 1.0 and scores[cid] > 0: scores[cid] *= pen` (index 인자 이미 있음 → 배선 0).
- **/similar** `index.similar_by_vector`([:71-77](../../recommendation-server/engine/index.py)): `scores = np.where(scores>0, scores*_penalty_vec, scores)` (exclude -999 마스킹 전, positive-part라 -999 무영향). **+ minimal-tier 제외**: `_exclude_similar`(build_desc_matrix 파생 minimal bid 집합) 인덱스의 score를 -inf 처리 → /similar는 *정밀* surface(항상 보임, 단일 비교)라 minimal 노출 안 함. /recommend는 *커버리지* surface라 minimal 유지. (api/similar.py:40,79·home.py:82 자동 커버.)
- **stage1 무페널티** — 후보 *선별*은 thin 포함(커버리지). 최종 랭크는 stage2가 결정.
- **쿼리책 무페널티** — 유저가 고른 책 신호는 온전히.
- **v3 폴백 무페널티** — prod 미도달(prestacked non-None, [cache.py:183](../../recommendation-server/engine/cache.py) 경고 로그). 의도적.

### 4.5 C5 — upgrade-only 불변식 + 재임베딩 (리뷰 M4)
- **불변식:** `source_tier`는 **개선만**(minimal→kakao_desc→rich), 역행 금지.
- **경합 해소(트리거 불필요):** 두 writer(배치 / 라이브 `ensure_books_embedded`)는 **insert-only(skip-if-present)** — 둘 다 "이미 있으면 skip"([user_embed.py:69](../../recommendation-server/engine/user_embed.py), [generate_book_v3_vectors.py:140](../../scripts/generate_book_v3_vectors.py)). M3 동등성으로 **같은 책 → 같은 (text,tier)** 이므로 동시-최초쓰기조차 *멱등*(다운그레이드 불가). **tier 업그레이드는 오직 C5 재임베딩 전담**(insert-only writer는 절대 기존 행을 안 바꿈). DB 트리거 없음(CLAUDE.md: 트리거는 단위테스트 불가). (참고 R3: `backfill_v3_genre.py`는 l1/l2 FK만 update — source_tier/desc_embedding/source_text 무관이라 불변식 직교, 예외 아님.)
- **재임베딩 배치** `scripts/reembed_provisional.py`(또는 `--upgrade`): `source_tier != 'rich'` 행 + `books` 조인 → `build_desc_source` **재적용해 tier 재도출**. (a) tier 상승(특히 rich 승격) 또는 (b) §5 backfill로 임시라벨('kakao_desc')된 행의 정확 tier 교정 시 → 갱신. **R3 핵심: `source_text` 동일 시 OpenAI 재임베딩만 skip(embed-once) — 단 재도출 tier ≠ 저장 `source_tier`면 `source_tier`/`provisional` UPDATE는 항상 수행**(예: 실제 minimal 행은 source_text 불변이라 재임베딩 불필요하나 'kakao_desc'→'minimal' 라벨 교정은 필수. 안 하면 §5가 막으려던 오라벨이 'rich'→'kakao_desc'로 옮겨갈 뿐). daily-pipeline `enrich` 끝(rich 채운 뒤) 배선. OpenAI 게이트(임베딩 변경분만).

### 4.6 데이터 흐름
```
books (전체)
   │  generate_book_v3_vectors (C1+C2: 폴백·clean_html·source_tier)
   ▼
book_v3_vectors (rich / kakao_desc / minimal)        ◀── reembed (C5, upgrade-only, 라벨교정)
   │  build_index (C3: index._candidate_penalty)
   ▼
index.pkl (v4-prestacked 유지, VectorIndex 안에 페널티)
   │  serving: stage2 + similar(minimal 제외) (C4: positive-part 차등 down-weight)
   ▼
추천/유사: rich 우선, 빈약은 폴백 — 항상 "해낸다"
```

## 5. 데이터 / 마이그레이션
- `20260628000001_book_v3_vectors_source_tier.sql`:
  - `ADD COLUMN source_tier TEXT NOT NULL DEFAULT 'rich'` + `CHECK (source_tier IN ('rich','kakao_desc','minimal'))`.
  - **🔴 BLOCKER 수정(R2) — 기존 thin 행 backfill:** C1 `ensure_books_embedded`가 이미 prod 라이브라 `provisional=true` thin 행이 존재. 블랭킷 DEFAULT 'rich'는 이를 **rich로 오라벨 → 감점 0 + reembed 영구 skip**. 마이그레이션에서:
    `UPDATE book_v3_vectors SET source_tier = 'kakao_desc' WHERE provisional = TRUE;`
    (임시 보수값 — provisional 비트만으론 kakao_desc/minimal 구분 불가. C5 reembed가 `build_desc_source` 재도출로 **정확 tier 교정**. `!= 'rich'`라 reembed가 반드시 재방문.)
- `provisional`은 이미 적용([20260628000000](../../supabase/migrations/20260628000000_book_v3_vectors_provisional.sql)). 모든 읽기/쓰기 컬럼 명시(생성컬럼 없음). 신규 테이블 없음.

## 6. 비용 (Eden 승인 게이트) — **구현 전 count로 확정**
- **OpenAI(일회성):** 신규 = `books − book_v3_vectors` × ~$0.0001(text-embedding-3-large). count(무시할 egress)로 산정·보고 **후** 임베딩.
- **Supabase egress:** 배치 `books` 전체 텍스트 read(일회성) + 빌드 v3 전량 read(~70MB). 빌드 수동 게이트([[feedback_supabase_egress]]).
- **메모리 ⚠️ go/no-go도 count 게이트(R1):** 후보풀 N권 증가 시 `desc_matrix_f16`(권당 ~4KB) + 인덱스 `BookVectors.desc` 증가(thin은 reason 0). 현재 ~382/512MB. **임베딩 *전* count로 예상 메모리 산정 → 위협 시 books-cap/경량화 먼저**(OpenAI 매몰 후 RSS abort 방지). 빌드 RSS 게이트(>450MB abort) 최종 가드.

## 7. 검증 계획
DB 쓰기 경로 실검증 필수([[feedback_dryrun_limits]] / CLAUDE.md).
- **단위 TDD:**
  - **폴백 동등성(M3):** `build_desc_source` ≡ `_pick_source_text` — rich/kakao_desc/minimal/빈 + **HTML 포함 rich** 픽스처에 같은 `(text,tier)`.
  - **tier 문자열 단일성(R2):** `set(SOURCE_TIER_PENALTY) == CHECK 허용집합 == build_desc_source 산출집합`(드리프트 검출).
  - `fetch_target_books`: rich 없는 책 포함, `author` SELECT, 기존 제외 후 limit.
  - tier 기록: rich/kakao_desc/minimal upsert.
  - **C4 부호 안전(B1 회귀):** 음수 점수 후보 **랭크 안 올라감**; 양수 thin 감점; 동점 rich 우선; **rich 후보 0이어도 thin 추천 산출**(graceful degradation). 쿼리책 thin 무감점. 구 pkl `getattr` 폴백 무감점.
  - **/similar(M1):** thin 후보 감점, **minimal-tier 제외**(SIMILAR_MIN_TIER), kakao_desc/rich 노출.
  - **upgrade-only(M4):** insert-only writer가 기존 행 미변경(멱등); reembed만 tier 상승/교정. **R3: source_text 동일+재도출 tier 변동 → 재임베딩(OpenAI) skip하되 `source_tier` UPDATE는 수행**(relabel 보존); source_text·tier 둘 다 동일 시에만 완전 skip.
  - **마이그레이션 backfill(R2 blocker):** provisional=true 행이 'kakao_desc'로 backfill(rich 아님) + reembed가 재방문해 실제 minimal로 교정.
  - **dedup:** 같은 작품 rich vs thin → 페널티 후에도 **rich 판본 생존**.
  - **build 정합(M5):** `_candidate_tier` 키 ⊆ `bid_order`; `_penalty_vec`·`_exclude_similar`이 `_desc_bid_order`와 정렬.
- **prod E2E**(throwaway, [[ref_prod_e2e_throwaway]]):
  - rich 없는 책만 좋아요 6권 → /recommend 비어있지 않고 그 책 기반(신규 여정 §1.1).
  - **niche-역전 가드(R2 product):** *알려진 좋은 니치 thin 책*이 *평범한 rich 책*보다 **위에 랭크되는지** 측정(0.85가 과한 핸디캡 아닌지). 과하면 minimal 상향.
  - tier1 `similar`가 thin seed로 비지 않음.
  - memory OOM 0.
- **배치 실쓰기:** `daily-pipeline mode=small` → kakao_desc/minimal 행 실생성·tier 확인.
- **회귀:** 기존 rich-only tier2 유저 추천 동치(구 pkl/rich 후보면 점수 불변).

## 8. 성공 기준
- `books`에 있는 책이면(tier·인기 무관) 임베딩되어 후보·소스로 쓰임.
- rich 매칭 없어도 빈약한 책이라도 추천/유사 **비지 않음**.
- rich가 동점 thin보다 우선, **음수 점수 후보 미승격**(B1), **좋은 니치-thin이 평범 rich에 안 묻힘**(niche 가드).
- 앱 변경 0. OpenAI 인라인/요청경로 0. 메모리 OOM 0. 기존 추천/similar 회귀 0.

## 9. 배포 순서 (B2 — 크래시 방지)
번들 버전 불변이라 강제는 아니나 권장:
1. **마이그레이션 머지**(source_tier + backfill) — apply-migrations 자동.
2. **코드 배포**(C1·C3·C4·C5): recommendation-server push → Render. 신코드는 현 v4 인덱스에 `getattr` 폴백 → 무페널티 정상(현 인덱스 thin 없음). 무크래시.
3. **배치 임베딩**(C2) + **C5 reembed**(backfill 라벨 교정): mode=small 검증 → count·메모리 보고·승인 → 전량 embed-once.
4. **인덱스 재빌드**(수동 게이트): thin 책 + 페널티 포함 새 pkl → 커밋/배포. 매 단계 무크래시(구·신 pkl 모두 로드 가능).

## 10. 미해결 / 리스크
- **niche 역전(R2 product MAJOR):** minimal 페널티가 과하면 *얇은 메타데이터의 좋은 니치 책*(핵심가치 #3 대상)이 *풍부한 블러브의 평범한 베스트셀러* 아래로 묻힐 수 있음 — 본 설계가 보호하려는 가치의 정반대. 완화책: ① 페널티를 타이브레이크 수준(0.95/0.85)으로 약하게 ② minimal은 /similar에서만 제외(/recommend 유지) ③ E2E가 *명시적으로* 니치-thin > 평범-rich 검증(§7). 잔여 리스크 명시.
- **메모리 헤드룸:** 신규 권수 크면 512MB 위협 → §6 pre-build count 게이트 1차, RSS 게이트 최종.
- **stage1 무페널티:** thin이 top-700 일부 잠식 가능하나 700 넉넉 — 허용, 관측.
- **폴백 코드 중복·tier 문자열 4곳:** 배포 경계상 불가피, 동등성·단일성 테스트가 동기화 장치.
- **minimal 신뢰도:** title+author+genre(author NULL 시 title+genre, 시리즈 근접중복) 신호 약함 — 페널티+/similar 제외가 완충하나 완전 무해 아님(허용).
- **tier0 콜드스타트:** 본 설계 밖(loan_count 큐레이션). 온보딩 그리드 구현 시 노출 급감(§1.1).
