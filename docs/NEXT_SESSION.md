# 다음 세션 핸드오프 (2026-07-01 갱신)

> 이번 세션: **새로고침 속도·새 큐레이션 + 추천 캐시 데드락 근본수정(배포 완료)** +
> **책등(서재) 대개편** — 한글 세로쓰기 룰 확립, golden 실렌더 검증, 폰 설치까지.
> ✅ **책등 변경 커밋·머지 완료 (PR #30, `79133b0`).** 다음 세션 1순위 = 폰 재빌드 + 서재 최종 확인.

---

## ✅ 이번 세션 완료

### 1. 새로고침 속도 + 매번 새 큐레이션 (PR #28, merged·배포됨)
- **근본원인**: `onRefresh`가 `/home`·`/recommend`를 **순차 await**(합산 지연) + `/home`이 큐레이션을 **hour 버킷 캐시**해 같은 시간대엔 새로고침해도 동일.
- **수정**: onRefresh `Future.wait` 병렬화(대기 ~절반) + 서버 `/home?refresh=1` force-refresh(hour 캐시 히트 skip → `weighted_sample_one` 재샘플). 클라 `homeForceRefreshProvider` 플래그로 당겨서 새로고침일 때만 force, 일반 로드는 캐시.
- 파일: `api/home.py`(refresh param), `app/.../recommendation_service.dart`(?refresh=1), `recommendation_provider.dart`, `home_screen.dart`.
- ⚠️ **트레이드오프**: force-refresh는 /home을 매번 miss(재조립)로 만들어 웜 기준 ~0.8s→~2.3s. 큐레이션은 *보조* surface라 이 트레이드오프 재검토 여지 있음(추후).

### 2. 추천 캐시 데드락 + 인덱스-stale 근본수정 (PR #29, merged·배포됨)
- **데드락(실측)**: Eden `recommendation_cache.computing`이 6/29부터 `true` stuck. `recompute_recommendations`가 computing=true면 **무조건 skip** → 재계산이 재시작/OOM으로 중단되면 플래그 영구 true → 모든 재계산 skip → 캐시 영영 안 풀림 → `/home` Tier2가 매 새로고침 인라인 재계산(~8~17s). = Eden이 겪던 "너무 느림"의 진짜 정체.
- **수정 A**: `STUCK_COMPUTING_SEC=180` + `_age_seconds()` — computed_at 나이가 180s 초과면 stuck으로 보고 재계산 진행(자가치유).
- **수정 B(같은 클래스 버그)**: `/recommend`는 `computed_at > built_at` 체크로 인덱스 재빌드 시 재계산하는데 `/home` Tier2 재사용엔 없어서 **재빌드 후 옛 인덱스 추천을 계속 서빙**. `rec_cache_reusable()` 순수 헬퍼로 추출 + built_at 포함, 단위테스트.
- 파일: `engine/cache.py`, `api/home.py`, `tests/test_cache.py`. **서버 pytest 161 통과.**
- **Eden 캐시 복구**: 수동 리셋(band-aid)은 지양 결정. **다음 피드백/책추가 시 input_hash 변경 → 서버가 자연 재계산**(데드락 수정으로 안 멈춤). 수동 DB surgery 0.

### 3. 추천 품질 진단 (스코어링은 정상, 문제는 staleness였음)
- "취향 붕괴(호러/SF 소실)"로 의심했으나, **로컬 인덱스(built_at=prod와 동일)로 오프라인 전수 검증**하니 현재 13좋아요 기준 fresh 스코어링은 **다면적**(정유정 스릴러·히가시노 미스터리·김정 SF 등 포함). 붕괴는 **데드락이 캐시를 6/29(10좋아요)에 얼린 staleness** 탓. 스코어링 대공사 불필요. (오프라인 검증 스크립트는 scratchpad, 커밋 안 함.)
- Eden 좋아요 13권 **전부 인덱스에 있음**(커버리지 문제 아님).

### 4. 책등(서재) 대개편 ✅ **커밋·머지 완료 (PR #30, `79133b0`)** — `book_spine.dart`, `bookshelf_row.dart`, `test/book_spine_golden_test.dart`, `test/bookshelf_test.dart`, `test/goldens/spine_shelf.png`
레퍼런스 + 원 설계(MOODBOARD 단일컬럼 목업) + 핵심가치(인식+소유)로 **전체 룰** 확립:
- **위계**: 제목 dominant(11px) > 저자 secondary(8px). 부제 제거(본제목만), **저자 전체명**(역자만 제거 — 성만 금지, 동명이인).
- **한글 정방향 세로쓰기**(위→아래). 다단 방향 = **좌종서(왼→오른)** — 전통 산문은 우종서지만 *현대 짧은 간판·표지판성 2줄 세로쓰기는 좌종서가 관례*(나무위키 세로쓰기/방향).
- **word-aware 조판**: 어절(공백) 경계 우선 줄바꿈 → "히가시노 게이고" = `히가시노`|`게이고` (단어 중간 안 깨짐). 한 어절이 컬럼 넘으면 글자 폴백.
- 균일 폰트, **폰트축소·잘림 없음**(FittedBox/scaleDown 금지 — 책마다 크기 다르면 안 됨), Semantics(전체 제목/저자) 접근성.
- 책등 높이 190, 비율 66/28/6.
- **서재 선반 = 여러 선반 줄바꿈**(가로 무한스크롤 → 세로, PRODUCT_PLAN 5-3 "한 선반 5~7권, 넘치면 아래 선반"). `bookshelf_row.dart` LayoutBuilder row-packing.
- **검증**: `test/book_spine_golden_test.dart`가 AppleGothic 로드해 실렌더 golden(`test/goldens/spine_shelf.png`) 생성 → 저자까지 눈으로 확인. analyze 클린, 테스트 통과. **폰 설치 완료**(Eden 확인 중).

---

## 🔴 다음 세션 1순위: 폰 재빌드 + 서재 최종 확인
책등 커밋·머지 완료(PR #30). 책등은 **클라 전용**이라 서버 배포 무관, **앱 재빌드 필요**:
```
rsync -a --delete "<iCloud>/app/lib/" ~/curation_build/app/lib/   # iCloud → 빌드폴더 동기화 먼저
cd ~/curation_build/app && flutter run --release -d 00008140-001C34580A0B001C
```
그다음 Eden이 폰 서재에서 책등/선반 최종 확인 → 문제시 golden 루프(`flutter test --update-goldens test/book_spine_golden_test.dart`).

## 🔲 남은 것
- **폰 재빌드 후** Eden이 폰 서재에서 책등/선반 최종 확인 → 문제시 golden 루프.
- 추천 fresh 확인: 앱에서 책 추가/평가 변경 → 서버 재계산 → 다양한 추천 뜨는지(이미 가진 책 제거).
- (선택) force-refresh 트레이드오프 재검토 / /home DB 쿼리 병렬화로 웜 지연 단축(리스크: 공유 sync 클라).

## 📌 이번 세션 핵심 학습 (memory `feedback_root_not_bandaid`에도 저장)
1. **그때그때 band-aid 금지** — 근본원인을 확장성·유지관리로. 수동 DB surgery 최소화, 서버 로직은 서버가.
2. **UI만 고치지 말 것** — 서비스 핵심가치(서재=인식+소유, 유일한 차별점) 전달이 베이스.
3. **케이스별 대응 금지, 전체 룰**로. 레퍼런스 제대로 찾아서.
4. **실제 UI 직접 검증**(스크린샷/golden), 문제시 루프. "검증했다"면서 대충 보지 말 것(저자 3컬럼 놓쳤던 실수).
5. 원칙 다 줬으면 **묻지 말고 결정·진행**.

## 환경 메모
- 폰 설치: iCloud 밖 `~/curation_build/app`에서 `flutter run --release -d 00008140-001C34580A0B001C`(iOS 릴리즈, 7일 만료). lib 수정 후 `rsync -a --delete <iCloud>/app/lib/ ~/curation_build/app/lib/` 동기화 먼저.
- golden 재생성: `flutter test --update-goldens test/book_spine_golden_test.dart` → `test/goldens/spine_shelf.png` Read로 확인.
- 서버는 PR merge→main→Render 자동배포(~8.5분). CODE_REV 하드코딩이라 /health로 배포확인 불가(behavioral: /home?refresh=1의 cache_hit=false).
