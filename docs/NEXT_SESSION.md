# 다음 세션 핸드오프 (2026-07-02 갱신)

> 이번 세션: **화차 중복 근본수정** + **추천 반응성 Phase 1**(계산을 읽기→쓰기 시점으로) +
> **/home 인라인 블로킹 핫픽스**(느림·큐레이션 사라짐 진짜 원인). 모두 머지·배포·검증됨.
> 다음 1순위 = **Phase 2: 추천 계산 자체 단축(ANN/int8) + 품질 전수 재검증.**

---

## ✅ 이번 세션 완료 (전부 머지·배포)

### 1. 책등(서재) 마무리 (PR #30·#31, 지난 세션 이월분)
- PR#30 한글 세로쓰기 대개편 + PR#31 2컬럼 좌우 여백 보정. 폰 확인 완료.

### 2. 화차 중복 근본수정 (PR #32, `4a5a57b`)
- **근본원인**: `registerBook`이 넘어온 `book.id`를 무시하고 isbn만 봄. 추천/홈에서 담으면
  `toBook()`이 기존 books.id는 싣지만 isbn·source=null → insert 경로로 **새 null-isbn 복제행** 생성.
- **코드**: `resolveBookRef` 순수헬퍼 — id 있으면 기존행 재사용, isbn 있으면 upsert, 둘다없으면
  title+author 재사용후 insert. (`book_registration_service.dart`, 유닛테스트 4)
- **데이터**: 마이그레이션(`20260701000000_repoint_null_isbn_shelf_dups.sql`) — 기존 null-isbn 서재책 15개를
  정본 isbn'd 행으로 repoint + canonical_book_id 마킹. **삭제 없음**(book_v3_vectors RESTRICT FK).
  psql BEGIN/ROLLBACK 리허설로 SQL버그 사전차단. **prod 적용: null_isbn 16→0, 같은작품 중복 0.**
- **검증**: prod E2E throwaway PASS(복제행0·id재사용·unique가드). 폰 재빌드.
- ⚠️ 남은 판본중복: `화차` vs `화차(개정판)`(다른 ISBN 판본) — Eden "판본 유지" 선택, dedup 안 함.

### 3. 추천 반응성 Phase 1 (PR #33 `1c11aef` + 핫픽스 PR #34 `b01e39e`)
- **근본원인(코드리뷰)**: 앱은 좋아요/평가를 Supabase 직접 write → 서버 재계산 트리거 안 됨.
  재계산이 유저가 추천 열 때 `/recommend`·`/home`에서 **처음·동기·8~17s**(무료 CPU) 발생.
  → 저니 보상(맞춤추천)이 대기에 갇힘. `/home`이 길어 타임아웃 시 **큐레이션까지 사라짐.**
- **수정 = 계산을 읽기→쓰기 시점으로 (스코어링 무변경 = 품질 그대로)**:
  - 서버: `/recommend`·**`/home`** 둘 다 인라인 `try_compute_inline` **제거** → 백그라운드 재계산
    트리거 + 즉시 반환(이전recs+computing / 빈+computing). ⚠️Phase1(PR#33)에서 /recommend만 고쳐
    /home이 여전히 막혔던 걸 PR#34로 잡음(이게 Eden 느림의 진짜 원인).
  - 서버: `POST /recompute/{user_id}` 신규 — 앱이 담기/평가 후 fire-and-forget 호출.
  - 앱: `RecommendationService.triggerRecompute()` → `addBookToShelf`·`feedback submit`에 배선.
  - 앱: 추천 대기 죽은 스피너 → `_RecommendationSkeleton`(skeleton + "취향 분석 중 · 좋아요 N권
    살펴보는 중" labor-illusion). reduced-motion·Semantics.
- **검증**: 서버 pytest 164, 앱 test 48. behavioral E2E(throwaway 실 JWT): `/recompute` 202,
  `/recommend` 논블로킹, **`/home` 9~12.5s→2.8s**(배포후 실측). 폰 재빌드 완료.
- **근거 리서치**: numpy f16 matmul은 CPU 안티패턴 / 2-stage+ANN(hnswlib) 이 진짜 단축 /
  skeleton·labor-illusion(Buell&Norton 2011) 대기 UX. (Phase 2 근거)

---

## 🔴 다음 세션 1순위: Phase 2 — 추천 계산 자체 단축 + 품질 재검증
Phase 1은 계산을 **쓰기 시점으로 옮겨** 읽기(추천 열기)를 빠르게 했을 뿐, **계산 자체(8~17s)는
그대로**다. 좋아요 변경 **직후 즉시** 추천을 보면 여전히 재계산 대기(그동안 skeleton). 진짜 단축:
- **후보 = ANN(hnswlib, 9,483권 메모리내 즉시) 또는 int8 스칼라 양자화**(f16의 절반 메모리+SIMD).
  전수 f16→f32 업캐스트(`engine/twostage.py` 병목) 제거 → sub-2s 목표.
- **⚠️ 스코어링을 바꾸므로 추천 품질 전수 재검증 필수**(Eden 최우선, "취향 붕괴" 경계).
  기존 오프라인 전수검증 방식 + prod E2E throwaway로 다면성 확인.
- 상세 리서치: 세션 대화(2-stage retrieval·hnswlib/FAISS·int8 recall·perceived-perf).

## 🔲 남은 것
- **Eden 폰 최종 체감 확인**: 홈 새로고침 빨라짐 + 큐레이션 유지 + 평가후 skeleton. (세션 끝에
  "다음세션 준비"로 넘어감 — 명시적 OK는 다음 세션에 재확인.)
- 추천 fresh: 평가/담기 → 선제 재계산 → 다면적 추천 뜨는지, 이미 가진 책 제거되는지.
- (선택) 화차 판본 dedup / force-refresh 트레이드오프 재검토.

## 📌 이번 세션 핵심 학습
1. **근본원인 다층 확인** — Phase1이 /recommend만 고치고 /home을 놓쳐 "여전히 느림" 재발.
   앱이 실제로 뭘 호출하는지(/home + /recommend 둘 다) 코드리뷰로 확인해야 했음.
2. **dry-run 함정** — 마이그레이션 psql BEGIN/ROLLBACK 리허설이 실쓰기 전 SQL버그 잡음.
   RESTRICT FK(book_v3_vectors) 때문에 DELETE 금지 → repoint+canonical 마킹으로 우회.
3. **품질 무손상 우선** — 레이턴시 급해도 스코어링 안 건드리는 선(계산을 쓰기시점으로+UX)부터.
   진짜 계산 단축(품질 리스크)은 재검증과 함께 Phase 2로 분리.

## 환경 메모
- 활성 gh 계정 **`hyhuh0910`** 확인 필수 — 세션 중 `karrot`로 여러 번 리버트됨(`gh auth switch --user hyhuh0910`).
- 폰 설치: iCloud 밖 `~/curation_build/app`에서 `rsync -a --delete <iCloud>/app/lib/ ~/curation_build/app/lib/` 후
  `flutter run --release -d 00008140-001C34580A0B001C`(iOS, 7일 만료). 초기 "Could not run" = **폰 잠금** → 해제후 성공.
- 서버는 PR merge→main→Render 자동배포(~8.5분, 이번엔 ~10분). CODE_REV 하드코딩이라 /health로 확인불가 →
  behavioral(신규 라우트 404→존재 / /home 지연 9s→2.8s). psql 검증=pooler `aws-1-ap-south-1:6543/5432`, `SUPABASE_DB_PASSWORD`.
- prod 진단/E2E: service_role REST + admin API로 throwaway 유저(비번 로그인→실 JWT). 스크립트는 scratchpad(미커밋).
