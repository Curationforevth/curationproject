# 다음 세션 핸드오프 (2026-07-01 갱신)

> 이번 세션: **핵심가치·유저저니 기준 전수 리뷰 → 판단 → 저니 갭 구현**.
> 추천 엔진(서버)은 이미 탄탄. 막힌 건 앱 저니 표면 → **온보딩 P0 신규 구현 + 추천표시 버그 + 마일스톤 + 피드백루프 + 취향 surfacing + 파이프라인 57014** 를 전부 구현·머지·배포.

---

## ✅ 이번 세션 완료 (전부 main 머지)

| 작업 | PR | 검증 |
|------|----|----|
| `/similar` 500 회귀 수정 (desc-dedup 후 bv.desc=None → desc_of) | #23 | prod 500→200 E2E ✅ |
| **P0 온보딩** (Welcome→그리드 N/6→최애+감성태그→완료) | #24 | DB쓰기경로 prod E2E ✅ / **UI 기기검증 미완** |
| 추천 표시 버그 (`!hasFeedback` 이 온보딩 유저 추천 가림) | #24 | E2E로 발견·수정 |
| 마일스톤 배경 배선 (서재, dark레벨 좌표 fg) | #24 | analyze |
| 피드백→추천 invalidation (computing 상태로 안전) | #24 | analyze |
| 파이프라인 57014 = 잡 직렬화 (enrich 우선) | #25 | **다음 run 확인 필요** |
| 취향 surfacing (추천 카드에 책 대표 reason) | #26 서버 + #24 앱 | **prod E2E ✅ reason 10/10 라이브** |

**잡은 실버그 3개** (analyze/유닛으론 못 잡음): /similar 500 · 온보딩 배치쓰기 PGRST102(모든행 키 동일 요구) · 홈 추천 게이트가 온보딩 추천 가림.

**핵심 판단 (유저저니 5구간 병렬 리뷰):** 어려운 코어(추천 엔진=인기신호 0누수·벡터분리·스펙트럼보존)는 이미 옳음. 막힌 건 앱 저니 표면(온보딩 부재·추천 안보임·취향이유 미노출). → 백엔드 배관 아니라 저니 갭을 닫는 게 핵심가치 직결.

---

## 🔲 다음 세션 (우선순위순)

### P1 — 기기/라이브 검증
1. **온보딩·서재 UI 기기 UX 검증** (최우선). 빌드 `~/curation_build/app`(iCloud 밖, detritus 회피), 폰 직접설치(7일). 확인: 온보딩 그리드 선택감·최애/감성태그·완료 후 추천 노출, 서재 마일스톤 배경 전환, 추천 카드 reason 렌더. UI 디테일은 Eden 디자인 눈 필요.
2. **파이프라인 직렬화 효과 확인**: `gh workflow run daily-pipeline.yml`(또는 다음 스케줄 03:00 KST) → enrich→collect→discovery→refresh 직렬로 **57014 없이** 완주하는지, discovery `-u` 로그로 진행 보이는지. 마지막 풀 success 06-25 이후 첫 정상 run 확인.

### P2 — 후속 레버
3. **유저별 매칭 이유 surfacing** (현재는 책 단위 대표이유). 개인화("당신의 X 때문에")는 **인덱스에 reason 텍스트 적재 + 재빌드 필요**(egress 승인 게이트 + 메모리 335/512). 구현안: `build_index` 가 `reason_texts[bid]`(임베딩 순서정렬) 적재 → `twostage` 가 best cand-reason index 반환 → `api` map index→text → `models` 필드 → 앱. 구 index 대비 getattr 폴백. Phase-2 스코프.
4. **down-weight 계수 튜닝** (0.95/0.85 niche 역전 방지), tier0 콜드스타트 loan_count 대안, book_love_reasons C-lever 축적, input_hash 리뷰 *수정* staleness(기존 버그), build_index.py(REST) 정리.

---

## 운영 메모 (빠른 시작)
- **prod**: https://curation-recommendation.onrender.com `/health` → books_loaded=9483, code_rev=oom-mem-relief-20260629 (surfacing은 code_rev 미변경, reason 필드로 확인).
- **git push**: `hyhuh0910` 계정(개인). `gh auth switch --user hyhuh0910` 후 push/PR, 끝나면 `eden-huh_karrot`(회사 EMU) 복원. EMU 활성 시 개인레포 PR API 막힘. keychain 잠기면 `git -c credential.helper='!f() { test "$1" = get && echo username=hyhuh0910 && echo "password=$(gh auth token --user hyhuh0910)"; }; f' push`.
- **PR 머지=배포**: recommendation-server 변경은 머지 시 Render prod 배포(~100s~8.5분). app/·워크플로는 Render 무배포. **self-merge(자기 PR 머지)는 auto-mode classifier 가 차단 → Eden 명시 "배포해" 필요.**
- **인덱스 재빌드**: `gh workflow run index-direct.yml --ref main` (직접연결 psycopg, 안전). 절대 REST build_index.py 스케줄 금지(9483 규모서 DB 죽임).
- **prod E2E**: throwaway 유저 패턴(pm-agent memory `ref_prod_e2e_throwaway`), /tmp/curation_*_e2e.py 참고. prod 쓰기는 Eden 승인.
- **로컬 git**: main = origin/main 동기(이번 세션 브랜치 3개 삭제 완료). 클린.
