# 다음 세션 핸드오프 (2026-07-02 #14 진행 중 — 저니 리뷰 + Sprint A 홈 회복력)

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

## 🔲 다음 후보 (Eden 판단, #14 저니 리뷰 반영)
1. **Sprint B — 온보딩→첫 추천 체인 복구**: `completeOnboarding()` 후
   triggerRecompute(사실상 한 줄) + 5권 최소 UI 강제 + "서재가 시작됐어요" 완료
   연출. 신규 유저 activation 대비 수정 범위 최소 — ROI 최상.
2. **Sprint C — 서재 감정 보상**: 꽂히는 애니메이션 + 마일스톤 배경 동적 전환
   (AppColors.milestone* 은 정의만 있고 미적용) + 서가 뷰 피드백 미작성 배지.
3. **폰 재빌드 + 체감 확인**: PR#44 이후 앱 변경 누적(PR#47 포함). '읽었어요' 전이 /
   서재 상태 배지 / 진입 시 큐레이션 스켈레톤→표시 / 새로고침마다 셔플.
4. **배포 시간 단축 설계**: 이미지에서 index.pkl 256MB 분리(시작 시 다운로드) →
   배포 8.5~12분 → 2~3분대 + cold boot 도 단축(Sprint A 와 시너지).
5. top_n 700 체감이 무거우면 300/500 하향(config 한 줄, verify 하네스 재검증).
6. 큐레이션 2차 고도화: 리라이트 톤 개선 / 취향 기반 동적 제목(Spotify 패턴) /
   tier2+ 전용 테마 공급(클러스터 기반 심화 — 슬롯은 이미 우선 노출 준비됨).
7. stage1 랭킹 미스 케이스(recall 고정 유저) 원인 분석 — 취향 산발 유저 품질 레버.
8. 섹션 간 '판본' 중복 / 커버 http URL 3권 정리 / PRODUCT_PLAN §4-3 문서 현행화
   (평가 2단계 good/bad 가 정본). 미정제 keyword 잔여 3건은 주간 워크플로 자동(방치 OK).

## 환경 메모
- gh 계정이 세션 중 **5회+** `eden-huh_karrot` 로 리버트됨(push/commit 직전마다) — push 전 `gh api user --jq .login` 확인 필수.
- 로컬 `recommendation-server/data/index.pkl.sha256` 은 6/29 로컬 빌드 잔재(stale 해시)여서
  `index.pkl.sha256.stale-local` 로 개명해 둠(untracked). prod 는 이 파일이 없어 해시검증 skip — 정상.
- prod E2E throwaway 패턴: admin API 생성→비번로그인→실JWT. **user_books 시딩 시
  `status='finished'` 필수**(wishlist 기본값은 rating 금지 CHECK). 측정 후 user_books/
  recommendation_cache/user_state/auth user 정리(가짜 good 이 co-save 신호 오염 방지).
- 배포 확인: `/health.code_rev` + `last_recompute_timings` (이제 로그 없이 관측 가능).
