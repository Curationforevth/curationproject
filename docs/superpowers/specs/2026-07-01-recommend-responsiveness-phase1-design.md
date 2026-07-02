# 추천 반응성 Phase 1 — 계산을 읽기 시점에서 쓰기 시점으로

## Context (왜)

유저저니는 **담기/평가 → (탐색) → 추천 보기**. 그런데 코드리뷰 결과:
- 앱은 좋아요/평가를 **서버 경유 없이 Supabase에 직접** 씀 (`feedback_flow_provider.dart:122`, `book_registration_service.dart`). 서버의 재계산 트리거(`POST /feedback` → BackgroundTask `recompute_recommendations`, `api/feedback.py:50`)를 **앱이 안 부름** → 선제 재계산이 없음.
- 재계산은 유저가 추천을 여는 순간 `/recommend` 의 `try_compute_inline` 로 **처음, 동기, 8~17초**(무료 단일 vCPU) 돌아감 (`api/recommend.py`, `engine/twostage.py`).

결과: **핵심가치 #3(맞춤추천)=저니의 보상 순간이 하필 유저가 결과를 볼 때 동기 8~17s 뒤에 갇힘.** NN/g 기준 1초면 주의 이탈·3초면 이탈 2배라 UX로 성립 안 됨.

**리서치 근거(요약):** numpy f16 matmul은 BLAS half-precision 미지원으로 느린 게 정설(CPU에선 f32가 정답). 진짜 단축은 2-stage+ANN(hnswlib, 9,483권은 메모리 내 즉시)/int8 양자화지만 **스코어링을 바꿔 추천 품질 재검증이 필요** → Phase 2로 분리. 대기 UX는 skeleton(체감 20~50%↑)+labor illusion(Buell&Norton 2011: 작업 가시화가 가치↑)+optimistic가 근거. 상세: 세션 리서치 노트.

## Goals / Non-Goals

**Goals (Phase 1):**
- 유저저니에서 추천이 **반응성 있게** 느껴지도록: 비싼 계산을 임계경로(읽기) 밖으로.
- **추천 품질 무손상** (스코어링 알고리즘 미변경).
- 불가피한 잔여 대기는 **정직한 UX**(죽은 스피너 제거).

**Non-Goals (→ Phase 2):**
- 계산 자체의 알고리즘적 단축(ANN/int8/2-stage). 품질 전수 재검증 루프와 함께 별도.
- 유료 인프라.

## 설계 — 3개 변경

### ① 좋아요 변경 시 선제 백그라운드 재계산 (계산=서버, 트리거=앱)
- **서버** (`recommendation-server/api/recompute.py` 신규 or `feedback.py` 확장): 경량 엔드포인트 `POST /recompute/{user_id}` (`Depends(verify_jwt)` + `current_user==user_id`) → `background_tasks.add_task(recompute_recommendations, user_id, app_state)` → 즉시 `202`. 기존 `recompute_recommendations` 의 computing/stuck 가드 재사용(중복·데드락 없음, PR#29).
- **앱** (`bookshelf_provider.dart addBookToShelf`, `feedback_flow_provider.dart` 평가/리뷰 저장 후, 삭제): Supabase 쓰기 성공 직후 `RecommendationService.triggerRecompute(userId)` 를 **fire-and-forget**(await 안 함, 실패 무시, 로그만).
- 트리거 대안(DB 웹훅)은 인프라↑ 대비 이득 작아 보류(단일 클라). 불변식 = "좋아요가 바뀌면 서버 재계산이 곧 돈다".

### ② `/recommend` 인라인 블로킹 제거 (`api/recommend.py`)
- 캐시 미스 시 `try_compute_inline` 동기 스코어링 경로 제거/게이팅:
  - **이전 캐시(recs) 있음** → 그 recs + `meta.computing=true` 즉시 반환 + `background_tasks.add_task(recompute_recommendations)`.
  - **캐시 없음(첫-ever)** → `recommendations=[]` + `computing=true` 즉시 반환 + 백그라운드 재계산. (앱은 skeleton + /home 큐레이션 폴백.)
- 결과: `/recommend` 응답 **항상 <1s** (동기 스코어링 없음).

### ③ 앱 대기 UX: skeleton + labor-illusion (`home_screen.dart` 추천 섹션)
- `computing==true` 또는 로딩 시 `CircularProgressIndicator`(현 `home_screen.dart:321-324`) → **skeleton 서가/카드** 위젯.
- **진행 문구**(labor illusion): 예) "취향을 분석하고 있어요 · 좋아요 N권을 9,500권과 비교 중". 기존 "맞춤 추천을 준비하고 있어요…"(`home_screen.dart:337`) 대체·강화.
- 이전 추천 있으면 즉시 표시 + 옅은 "업데이트 중" 배지.
- `reduced-motion` 존중, 스크린리더 라벨.

## Data Flow
```
[담기/평가] 앱 → Supabase user_books 직접 write (기존)
          ↘ (신규) fire-and-forget POST /recompute/{uid}
                       → 서버 background: recompute_recommendations → recommendation_cache UPSERT
[추천 열기] 앱 → GET /recommend/{uid}
          → 캐시 hit(warm): 즉시 fresh recs (<1s)
          → 캐시 miss: 즉시 이전 recs+computing / 빈+computing (동기계산 없음) + background recompute
          앱: computing이면 skeleton+진행문구, recs 있으면 즉시 표시
```

## Error Handling
- `/recompute` 실패/느림 → 앱은 무시(fire-and-forget). 다음 `/recommend` 가 백그라운드 재계산으로 자연 복구(기존 경로).
- 재계산 중 중복 트리거 → computing 가드가 흡수(180s stuck 자가치유, PR#29).
- 서버 콜드스타트 → 별건(keep-alive는 선택, 본 스펙 밖).

## Testing / Verification
- **서버 pytest**: `/recompute` 가 202 + BackgroundTask 등록, JWT 불일치 403. `/recommend` 캐시미스가 동기 스코어링 안 하고 즉시 반환(computing) 단위테스트.
- **prod E2E throwaway**(user_books 경로 규칙): throwaway 유저 평가 write → `/recompute` 호출 → 캐시 computing→warm 확인(블로킹 없음).
- **앱**: `flutter analyze`+테스트. 위젯테스트로 computing 상태 skeleton 렌더.
- **실기기**(Eden): 평가 후 추천 열기 → 죽은 스피너 없이 skeleton+진행문구 → 몇 초 뒤 갱신. `/recommend` 응답 <1s(behavioral).

## 배포
- 서버 변경 = main 머지 시 Render 자동배포. 앱 변경 = 폰 재빌드. 브랜치 `perf/recommend-responsive-phase1`, gh `hyhuh0910`.
