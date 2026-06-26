# 백로그/설계 — 홈·추천 (2026-06-26)

실기기 첫 end-to-end 검증에서 나온 제품 갭들. 우선순위 순.

---

## [완료] P0 — user_state RLS 회귀 수정
책 추가가 42501로 전부 막히던 것. `refresh_user_state_single` SECURITY DEFINER 복원
(마이그레이션 `20260626000000`, PR #21 머지·적용). tier 승급 정상 동작 확인.

## [완료] 앱 — 세션 중 자동 리뉴얼 제거 + 당겨서 새로고침 / 커버 고해상도 / 로그인 nonce
- 피드백 시 추천·홈피드 자동 무효화 제거(서재만 갱신) + 홈 RefreshIndicator 추가 → 세션 중 큐레이션 고정, 당길 때만 갱신.
- 카카오 썸네일 → `fname` 원본 URL로 고해상도화(`core/utils/image_url.dart`).
- 카카오 로그인 nonce(해시 to 카카오 / 원본 to Supabase) 원본 반영 + `crypto` 의존성.

---

## [#4 설계] 추천 진행 인디케이터 — "맞춤 추천까지 얼마나 더"

### 문제
유저가 몇 권을 좋아요해야 맞춤 추천이 시작되는지 모름 → 언제까지 등록할지 막막.
실제 기준: **좋아요(👍, rating='good') 개수** 로 tier 결정
(`refresh_user_state_single`: <3=tier0, <6=tier1, **>=6=tier2(맞춤추천 ON)**).

### 설계
홈 추천 섹션("이 책은 어때요?")의 빈 상태 + 온보딩에 **진행 인디케이터** 노출.
- 데이터 소스: 앱이 이미 가진 `bookshelfProvider`에서 `rating=='good'` 개수 = `likeCount` (서버 호출 불필요, 즉시).
- 표시:
  - `likeCount < 6`: **"맞춤 추천까지 좋아요 {6 - likeCount}권 더!"** + 진행 바(likeCount/6).
  - `likeCount >= 6`: 추천 섹션 정상 노출(빈 상태 문구 제거).
- 위치: ① 홈 `_RecommendationSection` placeholder 자리 ② 온보딩/서재 상단 얇은 배너(선택).
- 카운트는 **클라이언트 계산**(taste 미완성과 무관하게 정확) — "피드백을 더 남기면" 식 모호 문구 대체.
- ⚠️ tier 임계(6)는 서버 `tier.py`와 **단일 출처**로 맞출 것(상수 공유 or 서버 메타로 내려주기). 하드코딩 6 두 곳 드리프트 주의.

### 주의 (실제 추천 품질과 별개)
인디케이터가 6을 채워도, 좋아요한 책이 인덱스에 없으면 추천이 빈약함(아래 "취향 추출" 참조).
→ 인디케이터는 "tier 도달"을, 취향추출은 "추천 품질"을 담당. 둘 다 필요.

---

## [#2] 홈 큐레이션 항상 ≥3개 보장 (서버)

### 현 상태
`engine/tier.py sections_for_tier`는 tier별 curation/trending을 이미 ≥3 템플릿으로 줌.
그러나 `home.py _drop_empty_sections`가 빈 테마(책 0권)를 제거 → 3개 미만으로 떨어질 수 있음.

### 수정 (서버, Render 자동배포)
`assemble_sections_for_user` 후 빈 섹션 제거 → **curation/trending 개수 < 3이면
`_sample_curation(personalization='general')`로 curation_cache(1,892개)에서 채워 ≥3 보장.**
중복 테마 제외(recent_curation_ids). 앱은 받은 만큼 다 그림(이미 구현, 변경 불요).

---

## [NEXT] 유저가 고른 **어떤 책이든** 취향 추출 (추천의 진짜 핵심)

### 원칙 (Eden)
유명책으로 온보딩을 강제하지 않는다. **유저가 뭘 고르든 거기서 취향을 뽑아낸다.**
("베스트셀러 아닌 나의 취향" = 제품 핵심가치)

### 현 갭
추천 = 책 description 임베딩 유사도. 임베딩은 **수집 파이프라인이 모은 책만** 처리.
유저가 카카오 검색으로 추가한 책은 `books`에 저장되나 **임베딩 안 됨**
(+ 카카오 설명 짧아 품질게이트 SKIP) → 인덱스 밖 → 취향 벡터 0.
실측: Eden 좋아요 7권 중 인덱스 1권, taste_vectors=0.

### 설계 (비동기 축적 — 요청경로 OpenAI 금지 방침 준수)
1. 유저가 책 추가/좋아요 → 그 book_id를 **임베딩 대기 큐**로(예: books에 needs_embedding 플래그 or 별도 테이블).
2. 배치/백그라운드 잡이 대기 책의 description 임베딩 → book_embeddings 편입.
   - 설명 품질: (A) 즉시(카카오 snippet으로라도) vs (B) 알라딘/YES24 보강 후. MVP=A로 시작, 후속 B 보강.
3. 임베딩 완료 → `refresh_user_top_taste_single` 재실행 → 취향 벡터·추천 갱신.
- 효과: 유저의 **실제 선택**에서 취향 형성. 인덱스도 유저 관심 책으로 자연 확장.
- 게이트: OpenAI 비용·Supabase egress(Eden 승인 [[feedback_supabase_egress]]).
