# 책 상세 시트 · 액션 영역 전면 재설계 (설계)

작성 2026-07-03 · #16 세션 · Eden 승인 완료(목업 기반)

## 배경 / 문제

미보유 책 시트의 액션 영역이 **비대칭 4버튼 산개** 구조였다:
`[읽는 중][읽었어요]` 텍스트 버튼 + `[🔖]` 정사각 아이콘(3가지 모양/크기) + 그 아래 중앙에
따로 노는 `관심 없어요` pill. 상태별로도 하단에 삭제/관심없어요가 흩어져 위계가 없었다.
Eden 4차 지적: "CTA 3개를 비대칭으로 깔지 말고 레퍼런스 컨벤션을 지켜라."

## 컨벤션 (레퍼런스 확정)

- **Netflix 상세**: 풀폭 프라이머리(재생) + **균일 뮤트 톤 아이콘 로우**(찜·평가·공유).
  부정 신호/제거는 로우에 빨강으로 넣지 않고 ⋯더보기·토글로.
- **왓챠피디아**: 균일 톤 아이콘 로우(보고싶어요·코멘트·공유).
- **Goodreads**: 상태 버튼(배지) 탭 = 상태 변경/되돌리기. "Remove"는 메뉴로 분리.
- **StoryGraph**: 완독 되돌리기 = "다시 읽는 중으로"(mark as currently reading again).
- **iOS HIG**: destructive = 빨강·마찰. 단 **되돌릴 수 있으면(실행취소) 마찰 요건 충족**.

**공통 원칙: 프라이머리 1개(풀폭) + 나머지 전부 동일 형태의 균등 아이콘 액션 로우.**

## Eden 확정 결정 (목업 비교 후)

1. **미보유 프라이머리 = "읽었어요"** (읽고싶어요 아님 — 피드백 루프 우선).
2. **읽은 책 되돌리기 = 읽는 중으로** (평가 있으면 평가 삭제). read⟷reading 토글 성립.
3. **Destructive 배치 = 로우 흡수** (⋯더보기 아님 — 산개 소멸 우선, 관심없어요는 이 앱 핵심
   취향신호라 상시 노출 정당화).
4. **로우 톤 = 뮤트** (빨강 아님 — Netflix/왓챠 균일 톤 방식. 실행취소로 마찰 충족).
5. **되돌리기 트리거 = 로우 아이콘(정본) + '읽은 책' 배지 탭(보조 경로, Goodreads)**.

## 최종 상태 매트릭스

| 서재 상태 | 프라이머리(풀폭) | 아이콘 액션 로우(균등, 뮤트) |
|---|---|---|
| 미보유 | 읽었어요 | 읽는 중 · 읽고싶어요 · 관심 없어요 |
| 찜(wantToRead) | 읽었어요 | 읽는 중 · 찜 해제 |
| 읽는 중(reading) | 다 읽었어요 | 삭제 |
| 읽은 책 · 평가無(read, rating=null) | 평가 남기기 | 되돌리기 · 삭제 |
| 읽은 책 · 평가有(read, rating≠null) | (내 평가 카드 유지) | 되돌리기 · 삭제 |

**제약(계획 중 발견):** 읽는 중 로우에서 `읽고싶어요`(찜 강등)를 **제외**했다 — 데이터 계층이
wishlist 강등을 막는다(`_handleBookmark` 서재 보유 시 no-op, `resolveShelfWrite` wishlist
강등 CHECK 방지, PR#41). 드문 액션이라 새 경로 없이 제외(린 스코프).

- 아이콘: 읽는 중=open-book · 읽고싶어요=bookmark · 찜 해제=bookmark-slash ·
  관심 없어요=eye-off · 삭제=trash · 되돌리기=undo(u-turn).
- 로우 아이템 = 세로 스택(아이콘 23px + 라벨 11px), `flex:1` 균등, 무테두리, hover=variant bg.
- 톤(목업 뮤트 모드 확정값): neutral 아이템 = icon `#334155` / label `#475569`.
  destructive(삭제·관심없어요) = icon+label `#8A94A6`(뮤트 그레이 — neutral보다 살짝
  가볍게 de-emphasize, 빨강 아님). 이 미세 구분이 뮤트 안에서의 HIG 절충.

## 컴포넌트 설계 (book_detail_bottom_sheet.dart)

기존 `ShelfAwareActions` / `NotInterestedAction` / `ShelfDeleteAction` 3개를 **단일 통합
위젯 `SheetActionArea`** 로 대체한다. 프라이머리 슬롯과 아이콘 로우를 상태로부터 순수하게 파생.

```
SheetActionArea(userBook, isLoading, callbacks...) →
  ├─ 평가有 read: MyRatingSection (기존 유지) + IconActionRow
  └─ 그 외: PrimaryButton(라벨=상태별) + IconActionRow(items=상태별)

IconActionRow(items: List<SheetAction>) — SheetAction{icon, label, onTap, tone}
```

- 프라이머리 라벨/콜백, 로우 아이템 목록을 `userBook?.status` + `rating` 으로 결정하는 **순수
  매핑 함수** `actionsForState(userBook)` 를 분리(테스트 타깃).
- 기존 콜백 재사용: `_handleReading`(→reading), `_handleRead`(→read+피드백),
  `_handleBookmark`(→wantToRead), `_handleDelete`, `_handleNotInterested`, `_handleOpenFeedback`.
- **신규 콜백 `_handleRevertToReading`** — 아래 데이터 경로.
- **찜 해제** = 기존 `_handleDelete`(wishlist 행 삭제 + 실행취소 스낵바) 재사용(라벨만 '찜 해제').
- **배지 탭 되돌리기**: `ShelfStatusBadge` 를 read 상태일 때 `onTap` 받는 버튼으로
  (Semantics 유지). read 아닐 땐 기존 비인터랙티브 배지.
- 하단에 흩어져 있던 `NotInterestedAction`/`ShelfDeleteAction` **Padding 블록 제거** —
  전부 로우로 흡수.

## 데이터 경로: 되돌리기 (신규 write path — 실쓰기 검증 필수)

`revertToReading(container/ref, UserBook)`:
- user_books 행 UPDATE: `status: read→reading`, **`rating=null`, `emotion_tags=null`,
  `review_text=null`** (평가 삭제 — CHECK "rating은 read에만" 위반 방지 + 의미상 평가 소멸).
- `bookshelfProvider` invalidate + `triggerRecompute()` (취향 벡터 변동 — read+rating
  제거는 스코어링 입력 변화이므로 재계산 트리거. `addBookToShelf`/`_handleNotInterested`
  와 동일 패턴).
- 기존 `addBookToShelf(reading)` 재사용 불가: `resolveShelfWrite` 가 **rating 보존**이라
  되돌려도 평가가 남아 CHECK/의미 위반. 그래서 **명시적 rating-clear UPDATE** 필요.
- 테스트 주입 위해 Supabase 호출부 분리(`revertToReadingWith(client, userBook)`) —
  기존 `removeFromShelfWith`/`restoreToShelfWith` 패턴.
- **⚠️ dry-run 한계**(CLAUDE.md): 생성컬럼·CHECK·트리거는 dry-run 통과함. 이 경로는
  **prod E2E throwaway** 로 실쓰기 검증(pm-agent `ref_prod_e2e_throwaway`):
  read+rating 유저 → revert → user_books.status=reading & rating/tags/review=null 확인
  + user_state 재계산 트리거 확인 + 정리.
- 되돌리기엔 실행취소 스낵바 **불필요**(reading 은 손실 아님, 다시 '다 읽었어요' 하면 됨 —
  단 이전 평가는 복구 안 됨을 UX 로 감수). 대신 스낵바 문구로 안내: "읽는 중으로 되돌렸어요".

## 테스트 갱신

- **shelf_aware_actions_test.dart** → `actionsForState` 순수 매핑 5상태 검증으로 전환:
  각 상태의 프라이머리 라벨 + 로우 아이템(개수·라벨·순서·tone) 고정.
- **book_detail_expand_test.dart**: 새 위젯 트리(SheetActionArea) 기준으로 셀렉터 갱신,
  peek/확장 전환·되돌리기 배지 탭 노출 회귀.
- **book_my_rating_test.dart**: 평가有 read = 내 평가 카드 유지 + 로우(되돌리기·삭제) 존재,
  '되돌리기' 탭 시 콜백 호출 검증.
- 신규: 되돌리기 데이터 경로 단위테스트(fake client) — status/rating-null 페이로드 고정.

## 완료 기준

1. flutter test 그린(위 4종 갱신 + 신규).
2. prod E2E throwaway 로 되돌리기 실쓰기 PASS.
3. **실기기 스크린샷** 5개 상태 + 되돌리기/⋯없음 확인(폰은 오늘 7/3 재빌드·신뢰 완료,
   [[project-ios-free-provisioning-7day]] 만료 7/10). 재빌드 절차로 설치.

## 범위 밖 (백로그)

- 관심없어요 되돌리기 UI / 목록 관리(스펙 v1 밖).
- /book(BookDetailScreen) 제거 여부(별도 결정 — 조회 역할은 이미 시트 흡수).
- Sprint C 서재 감정 보상.
