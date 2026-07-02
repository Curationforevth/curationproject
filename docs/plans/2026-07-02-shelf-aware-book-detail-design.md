# 서재 상태 인지 책 상세 UI 설계 (shelf-aware BookDetailBottomSheet)

> 2026-07-02. Eden 요청: "이미 서재에 있는 책이었으면 UI 자체가 다르게 나와야 해."
> PR#41(재등록 23505 근본수정)이 **데이터 계층**을 고쳤고, 이 설계는 **표현 계층**을 맞춘다.

---

## 1. 현황 확인 결과 — 진입점별 처리가 갈린다

| 진입점 | 서재 상태 반영 | 근거 |
|---|---|---|
| 검색 (book_search_screen) | ✅ "이미 서재에 있어요" 비활성 처리 | `shelfIsbns` 로드(book_search_provider.dart:75-85), screen:78·150 |
| 서재 (library_screen) | ✅ 자체 상태 UI ('다 읽었어요' = status update + /feedback 이동, :345-371) | 서재는 UserBook 을 직접 다룸 |
| **홈 추천/트렌딩/비슷한책 → BookDetailBottomSheet** | ❌ **무조회** | `_bookmarked = false` 하드코딩(:36), initState 는 impression 로그만(:40-46). 이미 서재에 있어도 새 책 UI(읽는 중/읽었어요/북마크) 그대로 |

- 검색의 체크는 **isbn 기준**이라 isbn 없는(minimal) 책은 놓칠 수 있음(부차 개선 후보).
- 바텀시트 갭이 바로 오늘 23505 에러의 UX 측면: 이미 찜한 책인데 "새 책"처럼 보였다.

## 2. 설계 원칙 (핵심가치 정렬)

- **서재 경험(가치 1)**: "이 책은 이미 내 서재에 있다"는 사실 자체가 뿌듯함의 일부 —
  상태를 숨기지 않고 배지로 보여준다.
- **피드백 루프(가치 2·3)**: 이미 읽은 책이면 다음 행동은 재등록이 아니라 **평가 확인/수정**
  — 루프를 닫는 쪽으로 유도한다.
- 데이터는 **이미 있는 것 재사용**: `bookshelfProvider`(FutureProvider<List<UserBook>>,
  bookshelf_provider.dart:10)가 홈 진입 시 이미 로드됨 — **추가 네트워크 0**으로
  book.id 매칭. `addBookToShelf` 가 이미 `ref.invalidate(bookshelfProvider)` 하므로
  등록 직후 상태도 자동 일관.

## 3. 상태별 UI 스펙 (BookDetailBottomSheet)

`ref.watch(bookshelfProvider)` → `firstWhereOrNull((ub) => ub.bookId == book.id)`:

| 서재 상태 | 배지(표지 옆) | 액션 버튼 영역 |
|---|---|---|
| 없음 (현행) | — | [읽는 중] [읽었어요] [🔖] — 현행 유지 |
| **wishlist(찜)** | `🔖 찜한 책` | [읽는 중] [읽었어요] [🔖filled] — 전이는 PR#41 로 동작. 북마크 재탭 = "이미 찜한 책이에요" 스낵바(no-op) |
| **reading** | `📖 읽는 중` | [**다 읽었어요**(primary, →read 전이+/feedback)] 단독. 읽는중/북마크 숨김 |
| **finished** | `✓ 읽은 책` (+rating 이모지: 👍/👎) | rating 있음 → [**내 평가 보기·수정**(→/feedback/{userBookId})] / rating 없음 → [**평가 남기기**(→/feedback/{userBookId})] |

- provider 로딩 중/에러 → 현행(새 책) UI 폴백. 잘못돼도 PR#41 데이터 계층이 안전망
  (전이/no-op — 에러 없음).
- 스낵바 문구 정합: `registerBook` 이 `ShelfWrite` 결과를 함께 반환하도록 확장
  (`({String id, ShelfWrite write})`) → "서재에 추가했어요" / "읽었어요로 옮겼어요" /
  "이미 서재에 있어요" 구분(_handleBookmark 의 '추가했어요' 오표기 해소, PR#41 잔여 항목).

## 4. 구현 계획 (앱 단독, 서버 무변경)

1. `bookshelfProvider` 조회 헬퍼: `userBookFor(ref, bookId)` (bookshelf_provider.dart).
2. `registerBook` 반환형 확장(id+write) — 기존 호출부는 id 만 쓰므로 record 필드 접근으로 호환.
3. `BookDetailBottomSheet`: 상태별 배지 + 버튼 분기(위 표). reduced-motion·Semantics 유지.
4. 스낵바 문구 분기(3곳: _handleReading/_handleRead/_handleBookmark).
5. (부차, 선택) 검색 쪽 shelf 체크를 isbn→book.id 겸용으로 보강.

### 테스트
- 위젯 테스트: 상태 4종별 버튼/배지 렌더 (bookshelfProvider override).
- 유닛: registerBook 반환형·resolveShelfWrite 기존 6종 유지.
- 폰 확인 시나리오: 찜한 책 열기(찜 배지+읽었어요 전이) / 읽는 중 책(다 읽었어요) /
  평가한 책(내 평가 보기) / 새 책(현행).

### 범위 밖 (명시)
- 찜 해제(unbookmark) 기능 — 현재 삭제 UX 자체가 없음, 별도 결정.
- library_screen 자체 개편 — 이미 상태 인지형이라 유지.
