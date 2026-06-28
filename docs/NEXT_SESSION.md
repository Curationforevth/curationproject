# 다음 세션 — 미해결 항목 전체 (2026-06-28 기준)

> 이 세션에서 **"유저가 고른 어떤 책에서든 취향 추출"(통합 취향 모델)**을 설계→리뷰→구현→머지→배포→prod E2E까지 완료(merge `403c18b`).
> 아래는 그 외 **아직 해결되지 않은 모든 항목**을 한 곳에 모은 것. 우선순위순.
> 상세 맥락은 pm-agent 메모리 `project_status.md`(handoff #8~) 및 각 스펙 참조.

---

## 🔴 P1 — 추천 품질의 다음 레버

### 1. 추천 후보풀 커버리지 ("베스트셀러가 아닌"의 완전 실현)
- **문제**: 이번 작업으로 *취향 벡터*는 유저 책에서 정확히 뽑지만, 추천 *대상 풀*은 여전히 정적 인덱스(`index.pkl`, 06-26 빌드, 대중서 편향). 취향이 맞아도 니치 책으로 이어지지 않음.
- **할 일**: 유저/타유저가 추가한 인덱스 밖 책(이미 `book_v3_vectors`에 embed-once됨)을 정적 인덱스 *후보*로 편입 + 대중·니치 한국서 보강.
- **게이트**: 대량 Supabase egress + OpenAI 재임베딩 + 인덱스 재빌드(수동 배포). Eden 승인 필요 (`feedback_supabase_egress` 메모리).
- **참고**: 이번 스펙 §3 OUT #1 (`docs/superpowers/specs/2026-06-28-user-taste-extraction-design.md`).

### 2. provisional 책 재임베딩 보강 배치
- **문제**: 유저가 카카오 짧은 설명으로 추가한 책은 `book_v3_vectors.provisional=TRUE`로 임시 임베딩됨(이번 세션 신설). 품질이 얕음.
- **할 일**: YES24/알라딘이 `rich_description`(≥200자) 채우면 provisional 행을 재임베딩하는 배치. daily-pipeline enrich 잡에 연결. (= handoff #3의 보류된 "B/embed-once 레버": `book_v3_vectors.source_text` vs 현재 `rich_description` 비교로 진짜 바뀐 것만.)
- **게이트**: OpenAI 재임베딩 비용.

---

## 🟠 P2 — User journey 완성도 (콜드스타트·온보딩 체감)

### 3. [앱] 추천 진행 인디케이터 — "맞춤 추천까지 좋아요 N권 더!"
- **설계 완료**(backlog `docs/backlog-2026-06-26-home-and-recs.md` [#4 설계]), 앱 구현만 남음.
- 홈 추천 placeholder 자리에 `bookshelfProvider`의 `rating=='good'` 개수 = likeCount로 진행 표시 (클라 계산, 서버 호출 X).
  - `likeCount < 6`: "맞춤 추천까지 좋아요 {6-likeCount}권 더!" + 진행 바.
- ⚠️ tier 임계(6)는 서버 `engine/tier.py`와 **단일 출처**로(상수 공유/서버 메타). 하드코딩 6 두 곳 드리프트 주의.
- 현재 앱은 정적 텍스트 "피드백을 더 남기면…"만 노출(`home_screen.dart` `_RecommendationPlaceholder`).

### 4. [서버] 홈 큐레이션 항상 ≥3개 보장 + pull-to-refresh 재샘플링
- **설계 완료**(backlog [#2]), 서버 구현 남음. Render 자동배포.
- (a) `home.py` `_drop_empty_sections` 후 curation/trending < 3이면 `_sample_curation(general)`로 채워 ≥3 보장(중복 테마 제외).
- (b) **pull-to-refresh 재샘플링**: 현재 `home_section_cache`가 `input_hash=user_state.updated_at+시간버킷`이라 같은 시간엔 동일 반환 → 앱 새로고침해도 큐레이션 안 바뀜. force-refresh 파라미터로 캐시 우회+재샘플링 필요. (앱 #3 pull 제스처는 이미 구현됨, 서버 재샘플링이 짝.)
  - 단 **개인 추천(personal)**은 `recommendation_cache`(input_hash 키, 시간버킷 아님)라 이번 취향추출 결과는 새로고침에 정상 반영됨. 이 항목은 *큐레이션 섹션* 한정.

### 5. [앱] 구글 로그인 `flow_state_not_found` 수정
- 앱 시작 시 unhandled (이전 OAuth 딥링크 잔여). 카카오는 완전 동작(handoff #7). 구글만 미해결.

### 6. 큐레이션 첫 노출 지연
- 첫 `/home`은 `home_section_cache` 미스라 계산(서버 warm이어도 수초). pre-warm or 로딩 스켈레톤.

---

## 🟡 P3 — 추천 고도화 (Phase 2 성격)

### 7. 취향 발견 surfacing — "이 책의 ~한 점이 취향에 맞아요"
- 핵심가치 #2(취향 발견)의 유저 가시화. 현재 매칭된 reason은 *내부 계산만* 되고 노출 안 됨.
- 매칭 reason 문자열을 추천 카드에 표시(저비용 — 데이터는 이미 twostage에서 계산됨). Phase 2.

### 8. C 레버 — 피드백→공유 `book_love_reasons` 축적
- user 리뷰(rating=good)를 LLM reason 추출 배치로(source='user_feedback', user_mention_count) → **모두를 위한** 책 벡터를 시간이 지날수록 정교화(ARCHITECTURE §5 미구현). 현재는 그 유저 추천만 개선(feedback_embedding).
- Phase 2 큰 작업, 지속 OpenAI 비용.

---

## 🐛 P3 — 알려진 버그 / 정리

### 9. input_hash 리뷰 *수정* staleness
- `compute_input_hash`가 `has_fb`(0/1)만 봄 → 리뷰/태그를 *수정*해도(feedback_embedding 이미 존재) hash 불변 → 재계산 누락. **첫 피드백은 정상**(0→1로 hash 변경). 수정 케이스만 영향.

### 10. curation_cache DISTINCT ON title 마이그레이션 (handoff #3 A 후속)
- 서빙 응답은 작품단위 dedup(engine/dedup.py) 적용됐으나, 큐레이션 테마 섹션(`curation_cache`)은 SQL cron 함수(`refresh_curation_cache_all`, `array_agg`)라 중복 판본 dedup 별도 마이그레이션 필요.

### 11. (cosmetic) feature 브랜치 정리
- `feature/user-taste-extraction` 머지됨(403c18b). 로컬+origin 브랜치 삭제 가능.

---

## 🏢 비즈니스/출시 게이트 (Eden 결정 대기 — handoff #6)

### 12. iOS 스토어 출시
- **App Store/TestFlight = Apple Developer Program $99/년 필수**(우회 불가). Eden 계정 가입 → Xcode 서명·아카이브·업로드.
- **개인정보처리방침 URL** 필수(소셜로그인/리뷰 수집). 초안은 작성 가능.
- 무료 대안 = 폰 직접설치(7일 만료, 주기적 재설치): `cd ~/curation_build/app && flutter run --release -d <iphone>` (빌드는 iCloud 밖 로컬 카피에서 — codesign detritus 회피).

---

## 운영 메모 (다음 세션 빠른 시작)
- **prod 라이브**: 추천서버 https://curation-recommendation.onrender.com, `/health` version=v4-prestacked, mem ~351/512.
- **레포 auto-deploy**: main push → recommendation-server Render 배포 + 마이그레이션 apply-migrations 자동. 로컬 검증(서버 `pytest tests/`) 먼저.
- **git push**: 활성계정 `hyhuh0910` 필요(eden-huh_karrot은 권한 X). **EMU라 PR API 막힘** → push 권한으로 직접 머지. keychain 잠기면 `gh auth setup-git` + credential.helper로 `gh auth token --user hyhuh0910` 주입. 끝나면 기본계정 복원.
- **prod 쓰기**(E2E 등): auto-mode 분류기가 차단 → Eden 명시 승인 필요. 방법=메모리 `ref_prod_e2e_throwaway`(throwaway 유저 자급, 끝나면 cleanup — book_v3_vectors가 books FK라 v3 먼저 삭제; 백그라운드 recompute가 cleanup 후 재임베딩하는 레이스 주의).
- **DB 쓰기경로 검증**: dry-run은 트리거/CHECK/FK 못 잡음 → mode=small 실쓰기 or throwaway prod E2E (CLAUDE.md).
