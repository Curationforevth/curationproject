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

## 🔲 다음 후보 (Eden 판단)
1. **DB 왕복 축소 소PR** — 남은 total 2.76s 중 I/O 가 ~1.4s(5왕복). db2 재read 제거
   (ensure_* 가 행을 in-place 갱신하므로 재read 없이 hash 계산 가능, cache.py) 등.
   합격선 sub-2s 까지 -0.8s. 스코어링은 더 이상 병목 아님.
2. **STAGE1_TOP_N 150→700 복원 검토** — stage2 가 싸져서 가능해짐(recall min 개선 레버).
   단 **결과가 바뀌는 품질 변경** → verify_equivalence 하네스로 별도 평가 + Eden 승인.
3. Eden 폰 체감 확인: 평가 → skeleton → 추천 갱신이 이제 ~3s 내인지.
4. (기록) `scorer.py` v3 폴백의 reduceat 말미-빈-세그먼트 잠재 크래시 — prod 도달 불가
   (v4 prestacked 상시)이지만 twostage 와 같은 패턴으로 수정 가능.

## 환경 메모
- gh 계정이 세션 중 **2회** `eden-huh_karrot` 로 리버트됨 — push 전 `gh api user --jq .login` 확인 필수.
- 로컬 `recommendation-server/data/index.pkl.sha256` 은 6/29 로컬 빌드 잔재(stale 해시)여서
  `index.pkl.sha256.stale-local` 로 개명해 둠(untracked). prod 는 이 파일이 없어 해시검증 skip — 정상.
- prod E2E throwaway 패턴: admin API 생성→비번로그인→실JWT. **user_books 시딩 시
  `status='finished'` 필수**(wishlist 기본값은 rating 금지 CHECK). 측정 후 user_books/
  recommendation_cache/user_state/auth user 정리(가짜 good 이 co-save 신호 오염 방지).
- 배포 확인: `/health.code_rev` + `last_recompute_timings` (이제 로그 없이 관측 가능).
