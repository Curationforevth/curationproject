# 다음 세션 핸드오프 (2026-07-03 #16 — 앱 복구 + 시트 재설계 머지 완료 + 로드맵 재정렬 + git 전면 정리)

## 🔥 다음 세션 #0: 서재 감정 보상 (가치① — 로드맵 2단계)

시트 재설계(1단계)가 **머지 완료**됐으니, 다음은 **서재 감정 경험**(명시된 "유일한 차별점",
현재 ~40%). 브레인스토밍부터 시작 권장. 구현 3덩어리:
1. **꽂히는 애니메이션** — 책 서재 추가 시 "톡" 삽입 모션(현재 드래그 호버만, 추가 모션 없음).
2. **마일스톤 배경 동적 전환** — `AppColors.milestone0/10/30/50/100` 상수는 정의됐으나
   온보딩에서 milestone0 고정 사용뿐. 권수(`milestoneLevel()`)별 서재/홈 배경 전환 미구현.
3. **서가 뷰 피드백 미작성 배지** — `unreviewedBooksProvider` 감지는 되나 서가(bookshelf_row/
   book_spine)엔 표시 없음(홈 '내 책' 섹션 CTA만 존재).
→ 핵심가치① "한 권 더 꽂고 싶다" 리텐션 루프의 감정 레이어. 프론트엔드만·저비용·고확신.
관련: 디자인 안이면 frontend-design+playground 함께([[feedback-design-skills]]).

## ✅ #16에서 완료 (전부 머지·정리)

- **시트 액션 영역 전면 재설계 (PR#65 머지)** — "풀폭 프라이머리 1 + 균등 아이콘 로우(뮤트)".
  - 구 `ShelfAwareActions`/`NotInterestedAction`/`ShelfDeleteAction`/`_BookmarkButton` 삭제
    → 단일 `SheetActionArea` + `IconActionRow` + 순수 매핑 `actionsForState()`.
  - **신규 되돌리기**(읽은책→읽는중, 평가 삭제): `revertToReading*`(bookshelf_provider) +
    로우 `되돌리기` 아이콘 + '읽은 책' 배지 탭(보조 경로). 실행취소 없음(린) — 되돌리면
    이전 평가 복구 안 됨(스낵바 안내만).
  - Eden 확정: 미보유 프라이머리=읽었어요 · destructive=로우 흡수 · 톤=뮤트(#8A94A6) ·
    배지 탭=표시. 제약: 읽는 중 로우=삭제만(wishlist 강등을 데이터 계층이 금지).
  - 검증: flutter test **125 그린** + analyze 클린 + **prod E2E throwaway 되돌리기 실쓰기 PASS**.
  - 설계 `docs/superpowers/specs/2026-07-03-sheet-action-area-redesign-design.md` /
    계획 `docs/superpowers/plans/2026-07-03-sheet-action-area-redesign.md`.
  - ⏳ **유일한 잔여(선택)**: 실기기 5상태 눈확인. 폰에 빌드 설치·실행됨(exit=0) — 문제 시 fix-forward.
- **PR#63 머지** — 다읽었어요 정본경로 회귀 테스트(#15의 "리뷰 대기" 건, cherry-pick 검증 후).
- **git 전면 정리** — PR#27(stale 7/1 핸드오프) 닫음. 로컬/원격 브랜치 전부 삭제·워크트리 정리 →
  **로컬·원격 모두 `main` 하나만, 0 ahead/behind, 워킹트리 클린, 열린 PR 0.**
- **앱 "먹통" 복구** — iOS 무료 프로비저닝 7일 만료(6/26→7/3 19:26)로 판명. 재빌드+폰 신뢰로 복구.
  **다음 만료 7/10 19:41** — 재발 시 [[project-ios-free-provisioning-7day]]. 폰엔 재설계 빌드(=현 main)+PR#64 커버 설치됨.

## 🗺️ 로드맵 (코드 검증 기반, Eden "순서대로" 승인)

핵심가치 성숙도 실측: **③추천=과성숙 / ①서재 감정경험≈40% / ②취향 페이오프=0%**(`/taste`
플레이스홀더뿐). 앱이 "추천 리스트"로만 작동, 비전의 "즐거움" 레이어가 빔. **순서:**
1. ✅ 시트 재설계(완료) → 2. **서재 감정 보상**(가치① — 위 #0) → 3. **취향 발견 페이오프**
(가치② — 취향 요약/프로필 화면, 백엔드 LLM 의존·데이터 성숙 후). 상세 [[project-roadmap-emotion-layer]].

## 미결/주의 (#16)

- git push/PR 전 `gh api user --jq .login` = hyhuh0910 확인([[feedback-git-push]]).
- #15 미결 그대로: 데일리 파이프라인 저녁 실행 완주 확인 / /book(BookDetailScreen) 제거 여부.
- 이번 세션 페르소나(product-manager + design-ux-researcher) 인라인 사용 — 로드맵 판단 근거.

---

# 다음 세션 핸드오프 (2026-07-03 #15 세션 종료 — 책 카드 탭 통일 + 전수 QA, PR#52~62·64)

## 🔥 #0 최우선 (Eden 지시): 시트 액션 영역 전면 재설계

**문제 (Eden 4차 지적 끝 결론):** 개별 버튼 땜질이 아니라 액션 영역 구조 자체가 잘못됨.
미보유 책 시트 = [읽는 중][읽었어요] 텍스트 버튼 + [🔖] 정사각 아이콘의 **비대칭 3버튼**
+ 그 아래 중앙에 따로 노는 '관심 없어요' pill = CTA 4개가 3가지 모양/크기, 위계 없음.
"누가 CTA를 3개 깔면서 저딴 비대칭 UI를 써. 레퍼런스 참고해서 컨벤션 지켜라."

**레퍼런스 컨벤션 (재설계 기준):**
- Netflix 프리뷰 시트: 풀폭 프라이머리(재생) 1개 + **균등 아이콘 액션 로우**(찜·평가·공유
  — 아이콘+짧은 라벨, 동일 폭 flex)
- 왓챠피디아: 균등 아이콘 액션 로우(보고싶어요·코멘트·평가)
- Goodreads: 단일 프라이머리 + 상태 변경은 드롭다운
- **공통 원칙: 프라이머리 1개(풀폭) + 나머지 전부 동일 형태의 아이콘 액션 로우.**

**제안 상태 매트릭스 (브레인스토밍 출발점 — Eden 확인 후 확정):**
| 서재 상태 | 프라이머리(풀폭) | 아이콘 액션 로우(균등) |
|---|---|---|
| 미보유 | 읽었어요 | 읽는 중 · 읽고싶어요 · 관심 없어요 |
| 찜 | 읽었어요 | 읽는 중 · 찜 해제 |
| 읽는 중 | 다 읽었어요 | 삭제 |
| 읽은 책(평가有) | (내 평가 카드 유지) | 삭제 |
| 읽은 책(평가無) | 평가 남기기 | 삭제 |
→ 하단에 흩어져 있던 삭제/관심없어요가 로우로 흡수되어 보조 액션 산개 소멸.

**프로세스 요구(필수):** ①brainstorming → ②**frontend-design + playground 스킬**로
시각 목업을 만들어 Eden 에게 먼저 보여주고 승인 후 구현(메모리 feedback_design_skills
— 이번에 안 지켜서 4회 왕복 낭비). ③구현 파일: book_detail_bottom_sheet.dart 의
ShelfAwareActions/NotInterestedAction/ShelfDeleteAction 일괄 대체 + shelf_aware_actions_test·
book_detail_expand_test·book_my_rating_test 갱신. ④변경 후 실기기 스크린샷 확인까지가 완료.

## #15 세션 로그 (전부 머지, 폰=PR#61 빌드 설치됨 / 서버=ni-serve-filter 배포됨)

- **PR#52** 데일리 크론 새벽→저녁(외부 API 새벽 타임아웃=6/25 후 스케줄 전멸 근본원인).
  **오늘 저녁 실행 완주 확인 필요.** / **PR#53** 온보딩 5권 문구 가치중심 / **PR#54**(별도
  세션) enrich 에러율≤5% exit0
- **PR#55** 책 카드 탭 통일 + 2단계 시트(peek 0.65↔확장 1.0, DraggableScrollableSheet).
  설계·계획: docs/superpowers/{specs,plans}/2026-07-03-unified-book-card-tap*
- **PR#56~59** 실기기 후속: 셰브론 제거 / **내 평가 카드**(MyRatingSection — /book 조회
  역할 흡수) / 알라딘 cover500 / 보조액션 톤 / **탭 dismiss 근본수정(시트=브랜치 내비게이터!
  브랜치 navigatorKey 전부 pop — PR#57 루트 popUntil 은 무효였음, 재현 테스트 3케이스)**
- **PR#60~62** 전수 QA(4방향 병렬+Fable 검증): **관심없음 서빙 전면 필터**(/similar 계열
  + 홈 캐시히트 — High 2건, CODE_REV ni-serve-filter-20260703) / 다읽었어요 raw write→
  addBookToShelf 정본(완독 신호 유실) / _handleReading showTimedSnackBar / orphan 정리
- **PR#64** 알라딘 구형 /cover/ 무접미사 변형 → cover500 (이기적 유전자 흐림 잔존 원인)

## 미결 (Eden 결정/확인 대기)

1. **/book(BookDetailScreen) 제거 여부** — 조회 역할이 시트로 흡수돼 순수 죽은 코드
   (제거 시 화면+book_detail_provider+구 상태선택 시트 일괄)
2. **task_ed2f0bde 별도 세션** — PR#61 과 중복 작업, 닫기 권고
3. 데일리 파이프라인 저녁 첫 실행(오늘 21~22시) 완주 확인
4. 폰 재빌드 1회 필요(PR#64 커버 수정이 폰에 미반영 — #0 재설계와 묶어서 한 번에 권장)
5. 백로그: Android 백버튼 온보딩[추측] / 다읽었어요 recompute 배선 자동테스트(칩 삭제됨)
   / 커버 http URL 3권(iOS ATS)

## 함정 기록 (#15)
- showModalBottomSheet 는 **브랜치 내비게이터**에 뜬다 — 내비 수정은 실제 라우터 토폴로지
  재현 테스트 필수([[feedback-ux-conventions]])
- 진입점 제거 시 그 화면의 **조회 저니**까지 전수 확인(/book 사건)
- UI 컴포넌트 지적 반복되면 개별 속성 땜질 중단 → 레퍼런스 컨벤션으로 영역 전체 재설계
- 알라딘 커버 변형 4종(coversum/150/200/무접미사) — highResCoverUrl 이 정본, 새 진입점은
  Book/RecommendedBook.fromJson 경유할 것
- macOS TCC 권한 회수 시 실행 중 프로세스엔 재부여 안 됨 — 앱 재시작 필요
- sonnet 서브에이전트가 무행동 종료+자식 스폰하는 사례 — 재디스패치 시 "직접 즉시 수행,
  위임 금지" 명시

---

## (이하 #14 기록)

# 이전 핸드오프 (2026-07-02~03 #14 세션 종료 — 저니 리뷰 + PR#47~51 전부 배포·검증·폰 설치)

> **#14 후반 (PR#48~51, 전부 머지·배포·검증):**
> - **PR#48 Sprint B**: 온보딩 전권 good 제거(최애만 — Eden 결정 "읽음만 표시, 평가는
>   묻기") + 완료 직후 recompute 트리거 + 5권 최소 강제 + "서재가 시작됐어요" 연출 +
>   피드백 2초 후 추천 섹션만 자동 재조회(#11 정책 부분 개정, Eden 승인). flutter 85.
> - **PR#49 책 삭제 + 관심없음 신호** (Eden: "잘못 넣은 책을 뺄 수 없다" / "관심없는
>   책 안 뜨게 + 취향 반영"): 바텀시트 '이 책 삭제'(확인 없이 + 실행취소 스낵바,
>   스냅숏 복원) + 내책/읽고싶은책 카드 **길게 누르기**→시트. `user_book_signals`
>   테이블(RLS 리허설 PASS) + 스코어링: NI stage1 완전 제외 + desc **-0.75**,
>   wishlist desc **+1.0** 신규(config 상수, 양 스테이지) + **input_hash 에
>   status+signals 포함**(전 유저 캐시 1회 무효화 의도) + 서빙 즉시 필터.
>   검증: pytest 224 + 실인덱스 리포트(NI 제거 20/20·유사작 하락 80%·WL 상승
>   100%·안정성 75~82%) + **prod E2E throwaway PASS**(실 JWT RLS insert →
>   /recommend 즉시 제외 + 캐시 무효화 확인, 정리 완료). 리뷰가 잡은 함정:
>   pop 된 위젯 ref 사용 → ProviderContainer 캡처로 수정.
> - **PR#50 배포 단축**: index.pkl 을 이미지 밖으로 — GitHub Release 롤링 태그
>   (index-latest, 자산 --clobber) + 부팅 시 ensure_index_present()(원자적 .part,
>   Content-Length 검증, 3회 재시도 fail-loud) + .dockerignore + index-direct.yml
>   릴리즈 업로드를 push **앞에**(순서 중요). **실측: 배포 8.5~12분 → 회당 ~3~4분.**
> - **PR#51 실기기 버그 2건**(Eden): ①스낵바 영구 고착 — iOS accessibleNavigation
>   에선 액션 스낵바가 자동 해제 안 됨 + 큐까지 블로킹 → showTimedSnackBar(타이머
>   강제 close + clearSnackBars) ②시트 하단 터치 불가(미보유 책은 버튼 3개로
>   길어져 오버플로) → maxHeight 0.85 + SingleChildScrollView + '관심 없어요'
>   아웃라인 버튼(비활성처럼 보였음).
> - **폰 설치 완료(7/3, PR#51 포함 전부)** — 스낵바 자동해제/시트 스크롤까지 반영됨.
> - CODE_REV 는 PR 마다 범프할 것(이번에 두 번 잊어 관측 공백) — 현행
>   `index-out-of-image-20260702`.

---

## (이하 #14 전반 기록)

> **#14 (같은 날 오후):** Eden 리포트 "진입 시 큐레이션 안 뜸" 진단 → 핵심가치·유저저니
> 4방향 코드리뷰 → **PR#47(Sprint A: 홈 회복력) 머지·prod 검증 완료**.
> - **진단**: keep-alive GitHub 크론이 Actions 스케줄 스로틀로 **하루 ~8회만 실행**
>   (10분 크론인데 간격 2~4h, 5일 실측) → 서버 대부분 sleep → 진입 거의 항상 cold
>   wake 20~30s. 앱 큐레이션 섹션은 로딩/에러 시 무표시 + 에러 세션 고착.
>   /home 실측(throwaway): warm+캐시 0.4~0.7s / warm+미스 4.0s / cold 20~30s.
> - **PR#47**: ①keep-alive → **pg_cron+pg_net** 이전(같은 KST 06~02 윈도우, psycopg
>   BEGIN/ROLLBACK 리허설 → 배포 후 첫 실행 08:40Z succeeded + HTTP 200 확인)
>   ②큐레이션 스켈레톤+백오프 자동재시도(5/15/30s→수동 전환) ③서재 실패 하드블로킹
>   →배너+홈 생존 ④computing 스켈레톤 6s 폴링(최대 60s→수동). flutter test 73.
>   **⚠️ 폰 재빌드 필요**(아래 §7 rsync 절차, PR#44 이후 누적분).
> - **저니 리뷰 확정 갭(코드 직접 검증)**: ⓐ`completeOnboarding()` 이 recompute
>   미트리거 → 첫 세션 "맞춤 추천" 가치 유실(computing 고착의 온보딩 측 원인)
>   ⓑ온보딩 5권 최소 미강제(`_selected.isNotEmpty` 로 1권 진행 가능) ⓒ서재 감정
>   보상 미구현(꽂히는 애니메이션 없음·마일스톤 배경 정적 — 핵심가치1 "한 권 더"
>   루프) ⓓ서가 뷰에 피드백 미작성 표시 없음.
> - **기각/교정(리뷰 에이전트 오답 — 그대로 받으면 사고)**: 'neutral 평가 복구'는
>   DB CHECK(good/bad, 20260407 정규화)가 정본이라 기각 — PRODUCT_PLAN §4-3 이
>   구버전(문서 갱신 대상). '피드백 후 추천 미갱신'은 #11 Eden 정책(세션 중 자동
>   리뉴얼 제거)의 의도된 결과 — 결함 아님, 아래 결정 질문으로 승격.
> - **Eden 결정 대기 2건**: ①피드백 직후 추천 섹션만 자동 재조회 허용?(권장: 허용 —
>   서버는 이미 선제 재계산하므로 재조회만 추가) ②온보딩 전권 rating='good' 유지?
>   (권장: 유지 — 추천 부트스트랩이 피드백 수집 유도보다 우선)

---

> 이전 세션(#13) = **PR#35~#46, 12개 전부 머지·배포·prod 검증** (서버+앱+마이그레이션):
> ①**추천 재계산 63.5s→2.76s(×23), 품질 무손상 증명** → top_n 700 확대(현실형 recall
> 98.9%, warm ~5.7s) ②앱 '읽었어요' 23505 근본수정 + 서재 상태 인지 UI(폰 설치완료)
> ③홈 대공사: 큐레이션 LLM 정제(무의미 키워드 92%→0)·섹션 중복 제거·저자 정규화
> 3층 동기·커버 필수·tier2 두 번째 큐레이션 슬롯 부활·화제의 책 셔플.
> 상세 §1~12. 설계·실측: `docs/plans/2026-07-02-phase2-recommend-speedup-design.md`

---

## ✅ 이번 세션 완료 (전부 머지·배포·prod 검증)

### 1. PR#35 — recompute 스테이지별 계측 (`recompute-timings-20260702`)
- `engine/cache.py` 스테이지별 perf_counter + 로그 + `/health.last_recompute_timings`.
- **prod baseline 확정**: total 63.5~73.5s 중 **s1=47~48s + s2=15~17s (스코어링 97%)**.
  기억된 "8~17s"보다 훨씬 나빴음 — 좋아요 14권 유저가 평가 후 신선한 추천까지 1분+.

### 2. PR#36 — 벡터화 + BLAS 스레드 고정 (`twostage-vectorized-20260702`)
- **핸드오프의 ANN/int8 방향을 수정** — 진짜 병목은 upcast 가 아니라 반복 호출 구조:
  ① stage1 이 선형항(pb/fb)을 항별 matvec 루프로 돌던 것 → 단일 결합 쿼리벡터로 접음
  ② stage2 가 후보150×쿼리책~25 이중 Python 루프에서 같은 쿼리 reason 을 후보마다
  재업캐스트 → concat + `np.maximum.reduceat` (scorer.py v3 경로의 검증된 패턴)
  ③ Dockerfile `OPENBLAS_NUM_THREADS=1` — 0.1 vCPU 쿼터에서 멀티스레드 BLAS 경합 제거.
- **ANN(hnswlib) 보류 근거 확정**: stage1 은 하이브리드 점수(max-over-good+정규화)라
  ANN 대체 시 후보 의미가 바뀜(취향 리스크) + f32 내부저장 +76MB + N=9,483 은 exact 로
  충분. int8 은 numpy 에 커널이 없어 오히려 느려짐(기각). N≥50k 시 재검토.
- **결과**: s1 0.75s(×63)·s2 0.59s(×25)·total 2.76s(×23), memory 349MB(동일).

### 3. 품질 전수 재검증 (Eden "취향 붕괴" 경계 — 전부 통과)
- L0: pytest **186**(동등성 22 신규). TDD 가 실제 버그 검출 — 말미 빈-reason 후보의
  reduceat 경계(클램프 시 직전 후보 마지막 reason 이 max 에서 누락). 수정 후 green.
- L1: 실인덱스 108명(페르소나18+랜덤50+클러스터30+주입10) **전원 top-20 동일**,
  후보 overlap 150/150, max|Δ|=5.4e-06. `scripts/verify_equivalence.py`(재사용 가능).
- 실유저 5명(Eden 24권·fb14·인덱스밖2 포함) 오프라인 동일 확인.
- **prod 스냅숏**: 동일 throwaway 서재로 배포 전후 재계산 → **top-20 완전 동일(20/20)**.
- `engine/twostage_reference.py` = 직전 구현 verbatim 보존(기준선). 스코어링을 의도적으로
  바꾸기 전까지 수정 금지 — 이후 어떤 최적화든 이 기준선과 비교하면 됨.

### 4. PR#37 — recompute DB 왕복 축소 (`recompute-io-slim-20260702`, 같은 날 후속)
- db2 재read 제거(ensure_* in-place 갱신 행이 곧 스코어링 입력 → 그대로 해싱=코히런스)
  + ensure_books_embedded 인덱스-밖 필터(평상시 0콜) + flag 는 기존 행 UPDATE(recs 미전송).
- **근본수정(부수 발견)**: save 가 live hash 불일치로 skip 할 때 computing 미해제 →
  다음 트리거가 STUCK 180s 까지 갇히는 잠재 데드락 → skip 시 computing=false.
- pytest **193**(I/O 계약 7 신규). prod 실측: I/O 1.4s→**0.86s**, embed skip 0.0 확인.
  단 s1 이 0.75~1.6s 로 출렁(무료 CPU 이웃 소음) — warm total **2.3~3.3s** 밴드.
  sub-2s 상시 달성은 스코어링 분산 탓에 미완(다음 레버는 f32 행렬 상주화인데 +152MB 라 불가).

### 5. STAGE1_TOP_N 오프라인 평가 완료 (배포 없음 — Eden 결정 대기)
GT=stage2 전권 스코어링 대비 recall@20, 실인덱스 80명(랜덤40+클러스터40):
| top_n | 클러스터(현실형) avg/<90% | 랜덤 avg/<90% | prod s2 투영 |
|---|---|---|---|
| 150(현행) | 95.0% / 6/40 | 77.1% / 28/40 | 0.59s |
| **300** | **98.0% / 2/40** | 85.8% / 17/40 | **+0.6s (~1.2s)** |
| 500 | 98.8% / 1/40 | 89.9% / 13/40 | +1.3s |
| 700 | 98.9% / 1/40 | 91.0% / 12/40 | +2.1s (수확체감) |
- 메모리: 300 이면 CR transient ~11MB(안전). **권장=300**(현실형 98%, 레이턴시 +0.6s).
- ⚠️별개 발견: 일부 유저 recall 이 top_n 을 올려도 45~85%에 고정 — stage1 하이브리드
  랭킹 자체가 GT 상위책을 낮게 매기는 케이스(후속 품질 과제, min-max 정규화/pb 가중 의심).

### 6. PR#40 — STAGE1_TOP_N 150→**700** (Eden 승인·배포, `stage1-topn-700-20260702`)
- Eden 이 700 선택(현실형 recall 95→98.9%). **승인 게이트 검증이 실사고 방지**: 무분할
  stage2 로 700 돌리면 transient **175MB 실측**(후보풀이 reason-rich 편향: 평균 4.7 vs
  후보 ~15개) → 512MB 초과 위험. → `STAGE2_CHUNK=150` 후보 블록 처리 신설(40MB,
  top_n 무관 O(block), 블록 불변성 테스트). pytest 196.
- **prod 실측(700)**: warm total **5.7s** (s1 0.85 + s2 3.5 + I/O ~1.3), memory 347~369MB.
  150 대비 +2.5~3s — 승인된 트레이드오프. 아쉬우면 config 한 줄로 300(≈+0.6s)/500 조정 가능.

### 7. PR#41 — 앱 '읽었어요' 재등록 23505 근본수정 (Eden 실사용 리포트)
- **증상**: 북마크해둔 책에 '읽었어요' → "오류가 발생했어요". prod 재현 = 409/23505
  (user_books (user_id,book_id) UNIQUE, registerBook 이 무조건 INSERT).
- **근본원인**: 화차 fix(PR#32)가 books.id 를 올바르게 재사용하면서, 그전까지 null-isbn
  복제행 덕에 "성공"으로 위장되던 **기존 행 상태 전이 로직 부재**가 드러난 것(서버 배포 무관).
- **수정**: `resolveShelfWrite` 순수 전략 — 기존 행은 status UPDATE(rating 보존),
  wishlist 강등 금지(CHECK 위반 방지), 더블탭 레이스는 23505 캐치 폴백.
  flutter test 54·prod 시퀀스 검증(wishlist→finished→rating 전부 2xx).
- **⚠️ 폰 재빌드 필요**(앱 자동배포 없음): 잠금해제+케이블 후
  `rsync -a --delete <iCloud>/app/lib/ ~/curation_build/app/lib/ && cd ~/curation_build/app && flutter run --release -d 00008140-001C34580A0B001C`

### 8. PR#42 — 책 상세 바텀시트 서재 상태 인지 UI (Eden 요청, 폰 설치 완료)
- 홈 추천/트렌딩/비슷한책 바텀시트가 서재 상태 무조회('새 책' UI 고정)이던 갭 해소.
  Goodreads(버튼=현 상태의 다음 행동)·왓챠피디아 패턴 정렬, MOODBOARD 는 폐기(Eden 지시).
- `userBookForProvider`(bookshelfProvider 재사용, 네트워크 0) + `ShelfStatusBadge`
  (🔖찜/📖읽는중/✓읽은책·평가) + `ShelfAwareActions`(읽는중→[다 읽었어요], 읽은책→
  [내 평가 보기·수정]) + 스낵바 정합('옮겼어요'/'이미 서재에 있어요').
- flutter test 63. 설계: `docs/plans/2026-07-02-shelf-aware-book-detail-design.md`.

### 9. PR#43 — 큐레이션 품질 게이트 (Eden "계속 같은 큐레이션" 리포트)
- **진단**: 회전 로직 정상(24h 39개 상이 테마·책 겹침 0.1권). 원인 = 954개 중 874개(92%)가
  무정제 keyword 테마(형태소 조각 + "~관련 책들" 템플릿) → 지각 다양성 붕괴.
- `curate_theme_quality.py`(gpt-4o-mini 심사+리라이트, 불확실=kill 보수, 템플릿 desc 만
  = 증분) + 생성기 **insert-only 전환**(주간 upsert 가 리라이트 리셋·kill 부활시키던 함정
  + dry-run 실쓰기 버그 수정) + 주간 워크플로 후속 스텝(자동 증분 정제).
- **prod 적용 완료**: active 954→**408**(keyword 328·genre 21·author 59), 미정제 잔여 44.
- 레퍼런스: Spotify(맥락적 셸프 제목이 체감 다양성의 핵심)·밀리(리뷰 AI 추출 키워드 가공).

### 10. PR#44 — 홈 섹션 간 중복 제거 + 대표 저자 정규화 (Eden 스크린샷 리포트)
- 스크린샷 3이슈 조사: '첫 글자 유실'=가로 스크롤 잔상(정상). 실제 2건 근본수정:
- **섹션 간 dedup**: 홈 조립기에 seen_bids 전역 규칙(템플릿 순서=우선순위, 후보 넉넉한
  소스는 다음 후보로 채움). behavioral 검증: throwaway /home 중복 0건.
- **대표 저자 정규화 3층 동기**: books.author 소스별 표기('한강'/'이해 (지은이)'/'요한
  하리 지음')가 뿌리 — author 테마 44/61 오염 + top_authors 분산 + **by_author 매칭 누수**.
  `normalize_primary_author` 정본 규칙을 DB(마이그 20260702000000, 오염테마 비활성+전유저
  재계산)·Python(테마 생성)·Dart(displayAuthor, 전 노출부) 동일 적용. **psql 리허설이
  '지음' 꼬리 변형을 실데이터에서 검출**해 규칙 반영. 검증 후 by_author 큐레이션
  ("유시민 컬렉션")이 실제로 뜨기 시작 — 매칭 소생 라이브 증거.
- 잔여(의도): books.author 원본 보존(옮긴이 정보) / 판본 단위 섹션 간 중복(화차 판본
  결정과 얽힘) / 동일 저자 이표기 통합(entity resolution)은 범위 밖.
- pytest 213 + flutter test 68. 폰 3차 재빌드 설치 완료.

### 11. PR#45 — 홈 비주얼 서가 (Eden "이미지 안 나옴·새로고침 느림" 리포트)
- 서버/DB/CDN 레이턴시 정상 확인 후 3중 원인: ①캐시함수 author 분기가 원문 정확일치라
  정규화된 새 테마와 매칭 0 → **hourly cron 이 새 테마 자동 비활성화**(함정) ②캐시
  선정에 커버 조건 없음(앞 10권 중 5권 무커버) ③캐시 없는 갓 생성 테마가 뽑혀 빈
  섹션→드롭. → 캐시함수 정규화 매칭+커버 필수(마이그 20260702010000, 리허설 PASS),
  home.py 표시 방어+렌더가능 풀 제한.
- **게이트 스크립트 페이지네이션 버그**(supabase-py 1000행 캡 — 첫 실행이 절반만
  정제하고 완료된 척) 수정 후 완주: 활성 keyword 1,093→781(전부 리라이트), 잔여 3.
- 최종 behavioral: /home 4섹션 **커버 100%·중복 0**, author 컬렉션 정규화 매칭으로 책 수 증가.
- ⚠️함정 기록: supabase-py .execute() 기본 1000행 캡 — 전량 fetch 는 반드시 range 페이지네이션.

### 12. PR#46 — 화제의 책 셔플 + tier2 두 번째 큐레이션 슬롯 (Eden 질문이 버그 발견)
- Eden "왜 화제의책만 계속 보이나": trending=고정 anchor(12개월 대출수 top30, 일1회 갱신)
  → 지시로 **rank-가중 셔플**(Efraimidis-Spirakis, 선형감쇠 — 상위권 자주·매 조립 변화).
- Eden "큐레이션 실제 1개만 회전?": **버그 확인** — tier2 템플릿의 2번째 슬롯이
  personalization='tier2+' 인데 **생성기 4종 누구도 tier2+ 를 만들지 않음**(스펙 소비측
  vs 공급측 어긋남, 빈 섹션 자동드롭이라 조용히 실패). general 폴백으로 활성화 +
  요청 내 테마 중복 금지(picked_theme_ids). tier2+ 테마가 생기면 자동으로 우선 노출(스펙 복원 경로).
- prod 검증: 섹션 4→**5**, 큐레이션 **2칸** 매 새로고침 상이, 셔플 동작(교집합 6/10), 커버100%·중복0.
- 배포 느린 이유(Eden 질문): 이미지에 index.pkl 256MB 포함 — 매 배포 8.5~12분.
  개선 후보: 인덱스를 이미지 밖(시작 시 다운로드)으로 → 배포 2~3분대(다음 과제 후보).

## 🔲 다음 후보 (Eden 판단)
1. **[Eden 지시 백로그] 책 카드 탭 = 항상 바텀시트**: 어디서든(내 책·서재·검색·추천)
   책 카드를 탭하면 동일한 상세 바텀시트가 뜨도록 인터랙션 통일 설계.
   현재는 내 책 카드 탭=피드백/다읽었어요 CTA, 길게 누르기=시트로 이원화.
   기존 CTA 는 시트 안으로 흡수하는 방향 검토(브레인스토밍부터).
2. **Sprint C — 서재 감정 보상**: 꽂히는 애니메이션 + 마일스톤 배경 동적 전환
   (AppColors.milestone* 정의만 있고 미적용) + 서가 뷰 피드백 미작성 배지.
   핵심가치 1 "한 권 더 꽂고 싶다" 루프의 남은 큰 갭.
3. **폰 실사용 체감 확인**(Eden): 삭제/실행취소(4초 후 스낵바 소멸) · 관심없어요
   (아웃라인 버튼, 누르면 카드 소멸+이후 추천 변화) · 온보딩 새 플로우(5권 강제,
   완료 연출, 최애만 좋아요) · 피드백 후 ~2초 뒤 추천 자동 갱신.
4. top_n 700 체감이 무거우면 300/500 하향(config 한 줄, verify 하네스 재검증).
5. 큐레이션 2차 고도화: 리라이트 톤 / 취향 기반 동적 제목(Spotify 패턴) /
   tier2+ 전용 테마 공급(슬롯은 우선 노출 준비됨).
6. stage1 랭킹 미스 케이스(recall 고정 유저) 원인 분석 — 취향 산발 유저 품질 레버.
7. 관심없음 후속(스펙 v1 범위 밖 기록분): 되돌리기/목록 관리 UI · 큐레이션 카드에도
   관심없어요 노출 · 신호 가중치(-0.75/+1.0) 실사용 후 튜닝.
8. 정리류: 섹션 간 '판본' 중복 / 커버 http URL 3권 / PRODUCT_PLAN §4-3 현행화
   (평가 2단계 good/bad 정본) / repo 의 index.pkl LFS 축출 검토(릴리즈가 정본이 된
   지금 clone 비용만 남음). 미정제 keyword 잔여는 주간 워크플로 자동(방치 OK).

## 환경 메모 (#14 추가분 포함)
- **CODE_REV 는 서버 PR 마다 범프**(이번 세션 2회 잊음). 현행 `index-out-of-image-20260702`.
- 인덱스 정본 = GitHub Release `index-latest` 자산. index-direct.yml 은 릴리즈 업로드가
  commit/push **보다 먼저**(순서 바뀌면 새 부팅이 옛 인덱스를 받음). 배포 실측 ~3~4분/회.
- iOS accessibleNavigation(보조터치 등) 켜진 기기에선 액션 스낵바가 자동 해제 안 됨 —
  앱 전역에서 showTimedSnackBar 사용할 것(직접 showSnackBar 금지).
- 시트/오버레이 UI 는 pop 이후 위젯 ref 사용 금지 — ProviderScope.containerOf 캡처 패턴.
- PreToolUse 훅: sleep 없는 curl 루프, git 전체 스테이징(-A 플래그) 차단 — 명시 경로로.
- gh 계정이 세션 중 **5회+** `eden-huh_karrot` 로 리버트됨(push/commit 직전마다) — push 전 `gh api user --jq .login` 확인 필수.
- 로컬 `recommendation-server/data/index.pkl.sha256` 은 6/29 로컬 빌드 잔재(stale 해시)여서
  `index.pkl.sha256.stale-local` 로 개명해 둠(untracked). prod 는 이 파일이 없어 해시검증 skip — 정상.
- prod E2E throwaway 패턴: admin API 생성→비번로그인→실JWT. **user_books 시딩 시
  `status='finished'` 필수**(wishlist 기본값은 rating 금지 CHECK). 측정 후 user_books/
  recommendation_cache/user_state/auth user 정리(가짜 good 이 co-save 신호 오염 방지).
- 배포 확인: `/health.code_rev` + `last_recompute_timings` (이제 로그 없이 관측 가능).
