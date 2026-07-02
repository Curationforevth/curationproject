# 다음 세션 핸드오프 (2026-07-02 #13 갱신)

> 이번 세션: **Phase 2 — 추천 계산 자체 단축 완료.** 계측(PR#35) → 점수보존 벡터화 +
> BLAS 스레드 고정(PR#36) → prod 재계산 **63.5s → 2.76s (×23)**, top-20 전후 완전 동일.
> 설계·실측 전체: `docs/plans/2026-07-02-phase2-recommend-speedup-design.md`

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

## 🔲 다음 후보 (Eden 판단)
1. **폰 체감 확인**: '읽었어요' 전이 + 서재 상태 배지/버튼 + 홈 새로고침(새 큐레이션
   제목·중복 없음·저자 깔끔) — 오늘 배포분 전체.
2. top_n 700 체감이 무거우면 300/500 하향(config 한 줄, 재검증 하네스 있음).
3. 큐레이션 2차 고도화: 리라이트 톤 개선 또는 취향 기반 동적 제목(Spotify 패턴) 설계.
4. stage1 랭킹 미스 케이스(recall 고정 유저) 원인 분석 — 취향 산발 유저 품질 레버.
5. 섹션 간 '판본' 중복(제목+저자 기준) 처리 여부 — 화차 판본 유지 결정과 함께 재검토.

## 환경 메모
- gh 계정이 세션 중 **2회** `eden-huh_karrot` 로 리버트됨 — push 전 `gh api user --jq .login` 확인 필수.
- 로컬 `recommendation-server/data/index.pkl.sha256` 은 6/29 로컬 빌드 잔재(stale 해시)여서
  `index.pkl.sha256.stale-local` 로 개명해 둠(untracked). prod 는 이 파일이 없어 해시검증 skip — 정상.
- prod E2E throwaway 패턴: admin API 생성→비번로그인→실JWT. **user_books 시딩 시
  `status='finished'` 필수**(wishlist 기본값은 rating 금지 CHECK). 측정 후 user_books/
  recommendation_cache/user_state/auth user 정리(가짜 good 이 co-save 신호 오염 방지).
- 배포 확인: `/health.code_rev` + `last_recompute_timings` (이제 로그 없이 관측 가능).
