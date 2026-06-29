# 다음 세션 핸드오프 (2026-06-29 갱신)

> 이번 세션: **후보풀 커버리지(#1) 완수 + prod OOM 핫픽스 + 무료 인프라 한계 우회**까지 라이브 배포.
> `book_v3_vectors` 9,483권(=DB 전체 100%)이 추천 후보풀에 들어가 prod LIVE. memory 335MB(<512).

---

## ✅ 이번 세션 완료 (전부 prod 배포·검증됨)

### 1. 후보풀 커버리지 = "DB의 모든 책을 가진 정보 최대로 추천" (Eden 지시 실현)
- **스펙/계획**: `docs/superpowers/specs/2026-06-28-candidate-pool-coverage-design.md`(v3, 3라운드 적대적 리뷰), `docs/superpowers/plans/2026-06-28-candidate-pool-coverage.md`.
- **C1** 배치/라이브 임베딩 폴백 통일: `build_desc_source`(scripts) ≡ `_pick_source_text`(engine) — rich≥200 → 카카오 description → title+author+genre, `clean_html` 양쪽. (동등성 테스트 `tests/fixtures/source_tier_cases.json`)
- **C2** `generate_book_v3_vectors.py` rich 게이트 제거 → `books` 전체 임베딩.
- **C3** `source_tier`(rich/kakao_desc/minimal) 컬럼(마이그 `20260628000001`) + 인덱스 운반(`VectorIndex._candidate_tier`).
- **C4** 스코어러 **positive-part 차등 down-weight**(stage2 + /similar), minimal은 /similar 제외. `config.SOURCE_TIER_PENALTY={rich:1.0,kakao_desc:0.95,minimal:0.85}`, `SIMILAR_MIN_TIER`.
- **C5** `scripts/reembed_provisional.py` — tier 라벨 교정 + rich 승격 재임베딩, daily-pipeline enrich 배선.
- **임베딩 결과(DB)**: book_v3_vectors **9,483 = 100%** (rich 4774 / kakao_desc 3696 / minimal 1013).
- **stage1 청크**(`STAGE1_CHUNK=1024`): 요청당 f32 업캐스트 transient를 O(block) 고정.

### 2. prod OOM 핫픽스 (M1)
- `code_rev=oom-mem-relief-20260629`. dead l1/l2(W=0) 단일 zero strip + inline 동시성 `_COMPUTE_SEM` 2→1.

### 3. 메모리 — free Render 512MB 안에 9,483 인덱스 안착 (핵심 난관)
- **desc 3중복 dedup**: per-book `BookVectors.desc` strip(빌드) + 로드 시 번들 matrix `attach_desc_matrix`(중복 빌드 회피) → desc 1벌(−72MB). stage2는 `index.desc_of()`로 matrix 조회. (strip 전후 점수 maxdiff=0 검증)
- **`MALLOC_ARENA_MAX=2`** (Dockerfile): glibc 단편화 억제(Linux RSS가 Mac 대비 150MB+ 부풀던 주원인).
- **결과: /health memory_mb 335** (책 2.4배인데 기존 3904 인덱스 352보다 낮음).

---

## 🔧 이번 세션에서 확립한 인프라 사실 (다음 세션 필수 — 메모리에도 저장)

### 무료 Supabase 대용량 벡터 read 한계 + 우회
- **PostgREST(REST)로 235MB 벡터(9483×2000) read 불가** — 57014(statement timeout)/522/연결풀 포화로 빌드 3회 실패 + **DB 전체 응답불능까지 감.** ([[feedback_supabase_egress]] 그대로 — 재빌드 반복 egress가 원인.)
- **우회 = 직접 pooler 연결(psycopg3)**: `postgres.<ref>@aws-1-ap-south-1.pooler.supabase.com:6543` (transaction), `SUPABASE_DB_PASSWORD`(.env+GH secret). **psycopg2 금지**(transaction pooler와 "client encoding" 핸드셰이크 버그) → `psycopg[binary]`(v3). keyset 페이지네이션(`book_id > %s::uuid`, 빈문자열 금지).
- **DB 연결풀 포화 시**: Management API로 프로젝트 재시작(`POST /v1/projects/<ref>/restart`, `SUPABASE_ACCESS_TOKEN`) → 풀 클리어.

### 인덱스 재빌드 = `index-direct.yml` 워크플로 (신규, 정본)
- 직접 psycopg read + **기존 pkl reason 재사용**(재임베딩 0·OpenAI 0·DB 부하 최소) + Actions가 LFS 커밋. `recommendation-server/scripts/index_rebuild_direct.py`.
- **로컬 LFS push 불가**(업스트림 ~190MB에서 i/o timeout). 인덱스(281MB→dedup후 더 작음) 배포는 **반드시 Actions**로.
- ⚠️ **구 `build_index.py`(REST)는 9483 규모에서 DB 죽임 — 쓰지 말 것.** daily-pipeline build-and-recompute는 commit DISARMED 상태. 재빌드는 `gh workflow run index-direct.yml --ref main`.

### git/배포
- main = 모든 작업 + Actions가 커밋한 9483 `index.pkl`(github-actions bot). **로컬 main은 뒤처짐 + 로컬 data/index.pkl dirty(내 로컬빌드, 폐기) → 다음 세션 `git fetch && git reset --hard origin/main` 권장.**
- feature/candidate-pool-coverage 머지됨(삭제 가능).

---

## 🔲 남은 이슈 (다음 세션, 우선순위순)

### P1 — 라이브 검증
1. **prod E2E 검증 (미실시)**: throwaway 유저([[ref_prod_e2e_throwaway]])로 ① rich 없는 책만 좋아요 → /recommend 비어있지 않고 그 책 기반 ② **niche-thin > 평범-rich 가드**(down-weight 0.85가 좋은 니치책을 묻지 않는지 — R2 product 리뷰 핵심) ③ tier1 /similar이 thin seed로 비지 않음 ④ memory OOM 0. (코드는 단위테스트+dedup 동등성으로 검증됨, 라이브 E2E만 남음.)
2. **reembed_provisional 실동작 확인**: daily-pipeline enrich에서 kakao_desc/minimal 행이 rich 확보 시 승격되는지 + backfill 임시라벨('kakao_desc')이 실제 tier로 교정되는지.

### P2 — 후속 레버 (커버리지 스펙 OUT 항목)
3. **down-weight 계수 튜닝**: 0.95/0.85가 적정인지 E2E 품질로(niche 역전 방지).
4. **취향 발견 surfacing**: 매칭 reason "이 책의 ~한 점이 맞아요" 노출(Phase 2).
5. **book_love_reasons C-lever**: 유저 피드백→공유 책 reason 누적·정제([[feedback_data_lifecycle_refine]]의 미적용 영역).
6. **tier0 콜드스타트 큐레이션/트렌딩**: 여전히 loan_count(인기) 순 — "베스트셀러 아닌"의 콜드스타트 미달(별도 설계).
7. **input_hash 리뷰 *수정* staleness**(기존 버그): 리뷰/태그 수정 시 재계산 누락.

### P3 — 정리/주의
8. **메모리 헤드룸**: 현재 335/512(여유 ~177). desc dedup + arena=2가 load-bearing → 향후 인덱스 성장 시 /health memory 재확인 필수. 더 키우면 prestacked(169MB reason)이 다음 한계.
9. **build_index.py(REST) 정리**: index-direct.yml로 일원화하거나 build_index를 직접연결로 전환(현재 방치 시 9483 규모에서 실패).
10. **임시파일/브랜치 정리**: /tmp/extract_v3.py·local_index_build.py(일회성), feature 브랜치.

---

## 운영 메모 (빠른 시작)
- **prod**: https://curation-recommendation.onrender.com `/health` → books_loaded=9483, memory_mb~335, code_rev=oom-mem-relief-20260629.
- **git push**: `hyhuh0910` 계정. keychain 잠기면 `git -c credential.helper='!f() { test "$1" = get && echo username=hyhuh0910 && echo "password=$(gh auth token --user hyhuh0910)"; }; f' push`.
- **인덱스 재빌드**: `gh workflow run index-direct.yml --ref main` (직접연결, 안전). 절대 REST build_index.py 쓰지 말 것.
- **prod 쓰기/대량작업**: auto-mode classifier가 차단 → Eden 명시 승인 필요(이번 세션처럼).
