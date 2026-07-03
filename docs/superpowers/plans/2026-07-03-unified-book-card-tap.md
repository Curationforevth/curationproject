# 책 카드 탭 통일 + 2단계 확장 시트 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어디서든 책 카드 탭 = 동일한 2단계(peek→확장) 상세 바텀시트. 카드 내 버튼은 액션 직행 유지.

**Architecture:** `BookDetailBottomSheet` 를 `DraggableScrollableSheet`(snap 0.65/1.0) 기반으로 개편하고, 5개 노출 지점(홈 내책·서재 책등·서재 읽는중·검색·등록)의 탭 핸들러를 `BookDetailBottomSheet.show()` 로 배선. 시트/서버 데이터 계층 변경 없음.

**Tech Stack:** Flutter, Riverpod, 기존 `flutter test` 하네스 (`app/test/book_detail_test.dart` 패턴 재사용).

**Spec:** `docs/superpowers/specs/2026-07-03-unified-book-card-tap-design.md`

## Global Constraints

- 작업 루트: `app/` (모든 경로는 `app/` 기준). 테스트: `cd app && flutter test`.
- 스낵바는 반드시 `showTimedSnackBar`/`showDeletedSnackBar` (직접 `showSnackBar` 신설 금지 — iOS accessibleNavigation 함정).
- pop 이후 위젯 ref 사용 금지 — `ProviderScope.containerOf` 캡처 패턴 유지.
- 피드백 최단 저니 보존: 내 책 카드 [피드백 남기기]/[다 읽었어요], 읽는 중 카드 [다 읽었어요] 버튼은 **제거·우회 금지** (핵심가치 ② 연료선).
- 서버/DB 변경 없음. CODE_REV 범프 불필요.
- 커밋 메시지: `<type>: <한국어 설명>` + Co-Authored-By 트레일러.

---

### Task 1: BookDetailBottomSheet 2단계화 (peek 0.65 ↔ 확장 1.0)

**Files:**
- Modify: `lib/features/home/widgets/book_detail_bottom_sheet.dart`
- Test: `test/book_detail_expand_test.dart` (신규)

**Interfaces:**
- Produces: `BookDetailBottomSheet.show(BuildContext, Book)` — 시그니처 불변 (기존 호출부 5곳 무수정 호환). 확장 상태 토글 셰브론 `Key('sheet_expand_chevron')`, 확장 헤더 닫기 `Key('sheet_close_button')`.
- 시트 내부 상수: `_peekSize = 0.65`, `_minSize = 0.4`.

**선행 리팩터 (테스트 가능성):** `BookDetailBottomSheet.initState` 가 `Supabase.instance.client` 를 직접 불러 시트 전체를 pump 하는 테스트가 불가능했다(기존 테스트들이 서브위젯만 검증한 이유 — shelf_aware_actions_test.dart 상단 주석). 임프레션 로거를 provider 시임으로 뺀다:

```dart
// lib/core/services/impression_logger.dart 에 추가:
final impressionLoggerProvider = Provider<ImpressionLogger>(
    (ref) => ImpressionLogger(Supabase.instance.client));

// 시트 initState 에선:
if (widget.book.id.isNotEmpty) {
  unawaited(ref.read(impressionLoggerProvider)
      .logAction(bookId: widget.book.id, action: 'clicked'));
}
```

테스트는 `impressionLoggerProvider` 를 no-op 페이크(`SupabaseClient('http://localhost', 'anon')` 로 생성한 서브클래스, home_resilience_test.dart 의 페이크 클라이언트 관례)로 override 한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `test/book_detail_expand_test.dart`. 하네스는 ProviderScope overrides 로 직접 구성: `bookshelfProvider`(shelf_aware_actions_test.dart 의 UserBook 헬퍼 참고), `similarBooksProvider`(고정 리스트), `impressionLoggerProvider`(위 no-op 페이크). **주의: `test/book_detail_test.dart` 는 무관한 화면(피드백 별점) 테스트다 — 하네스 복사 대상 아님.**

```dart
// 핵심 시나리오 3개 (하네스 셋업은 book_detail_test.dart 참조):
testWidgets('peek 상태 — 셰브론 노출 + 설명 3줄 클램프 + 행동 버튼 접힘 없이 가시', (tester) async {
  await pumpSheet(tester, bookWithLongDescription); // 하네스 헬퍼: 반드시
  // BookDetailBottomSheet.show() 경유로 pump (DraggableScrollableSheet 포함 검증)
  expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  final text = tester.widget<Text>(find.byKey(const Key('sheet_description')));
  expect(text.maxLines, 3);
  // 홈→등록 2탭 저니 보존: 미보유 책 3버튼이 peek(0.65)에서 스크롤 없이 히트 가능
  expect(find.text('읽었어요').hitTestable(), findsOneWidget);
  expect(find.text('읽는 중').hitTestable(), findsOneWidget);
});

testWidgets('셰브론 탭 → 확장: 설명 전문 + 비슷한 책 그리드 + 닫기 헤더', (tester) async {
  await pumpSheet(tester, bookWithLongDescription);
  await tester.tap(find.byKey(const Key('sheet_expand_chevron')));
  await tester.pumpAndSettle();
  final text = tester.widget<Text>(find.byKey(const Key('sheet_description')));
  expect(text.maxLines, isNull);
  expect(find.byKey(const Key('sheet_similar_grid')), findsOneWidget);
  expect(find.byKey(const Key('sheet_close_button')), findsOneWidget);
});

testWidgets('확장 헤더 ✕ 탭 → 시트 닫힘', (tester) async {
  await pumpSheet(tester, bookWithLongDescription);
  await tester.tap(find.byKey(const Key('sheet_expand_chevron')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('sheet_close_button')));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sheet_description')), findsNothing);
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd app && flutter test test/book_detail_expand_test.dart` → FAIL (Key 없음).

- [ ] **Step 3: 구현.** `book_detail_bottom_sheet.dart` 변경 골자:

```dart
// show(): useSafeArea 추가 (확장 시 상태바 침범 방지). 시그니처 불변.
static Future<void> show(BuildContext context, Book book) {
  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    // 모달 자체 드래그를 끈다 — 내부 DraggableScrollableSheet 와 제스처가
    // 경합해 핸들을 끌면 리사이즈 대신 모달 통째 dismiss 되는 오동작 방지.
    // 닫기 = 바깥 탭 / 최소 높이까지 드래그(NotificationListener pop) / 확장 헤더 ✕.
    enableDrag: false,
    backgroundColor: Colors.transparent,
    builder: (_) => BookDetailBottomSheet(book: book),
  );
}

// State 에 추가:
static const _peekSize = 0.65;
static const _minSize = 0.4;
final _sheetController = DraggableScrollableController();
bool _expanded = false;

@override
void initState() {
  super.initState();
  // 임프레션: provider 시임 경유(위 선행 리팩터) + DB 미등록 책(id='') 스킵
  if (widget.book.id.isNotEmpty) {
    unawaited(ref.read(impressionLoggerProvider)
        .logAction(bookId: widget.book.id, action: 'clicked'));
  }
  _sheetController.addListener(() {
    final expanded = _sheetController.size > 0.9;
    if (expanded != _expanded) setState(() => _expanded = expanded);
  });
}

@override
void dispose() { _sheetController.dispose(); super.dispose(); }

// build(): 기존 Container(maxHeight 0.85 + SingleChildScrollView) 를
// NotificationListener + DraggableScrollableSheet 로 교체.
@override
Widget build(BuildContext context) {
  final book = widget.book;
  final userBook = ref.watch(userBookForProvider(book.id));
  return NotificationListener<DraggableScrollableNotification>(
    onNotification: (n) {
      if (n.extent <= _minSize + 0.01) Navigator.of(context).pop();
      return false;
    },
    child: DraggableScrollableSheet(
      controller: _sheetController,
      expand: false,
      initialChildSize: _peekSize,
      minChildSize: _minSize,
      maxChildSize: 1.0,
      snap: true,
      snapSizes: const [_peekSize],
      builder: (context, scrollController) => AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(_expanded ? 0 : 20),
          ),
        ),
        child: Column(
          children: [
            // 확장 헤더만 스크롤 밖 고정. peek 핸들은 스크롤 안 첫 요소 —
            // DraggableScrollableSheet 는 제공된 scrollController 의 스크롤러블
            // 위 드래그로만 extent 를 움직이므로, 핸들이 밖에 있으면 죽은 드래그가 된다.
            if (_expanded) _buildExpandedHeader(book),
            Expanded(
              child: SingleChildScrollView(
                controller: scrollController,
                child: Column(children: [
                  if (!_expanded) _buildPeekHandle(),
                  /* 기존 본문 그대로 이동 */
                ]),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

// peek 핸들: 기존 드래그바 + 우측 셰브론(접근성 대체 확장 진입).
Widget _buildPeekHandle() => SizedBox(
  height: 36,
  child: Stack(alignment: Alignment.center, children: [
    Container(width: 36, height: 4, decoration: BoxDecoration(
      color: AppColors.border, borderRadius: BorderRadius.circular(2))),
    Positioned(right: 8, child: IconButton(
      key: const Key('sheet_expand_chevron'),
      icon: const Icon(Icons.keyboard_arrow_up, color: AppColors.textSecondary),
      tooltip: '크게 보기',
      onPressed: () => _sheetController.animateTo(1.0,
        duration: const Duration(milliseconds: 250), curve: Curves.easeOut),
    )),
  ]),
);

// 확장 헤더: 제목 + ✕ (Google Maps 모프 패턴)
Widget _buildExpandedHeader(Book book) => Padding(
  padding: const EdgeInsets.fromLTRB(24, 8, 8, 0),
  child: Row(children: [
    Expanded(child: Text(book.title, maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600,
        color: AppColors.textPrimary))),
    IconButton(
      key: const Key('sheet_close_button'),
      icon: const Icon(Icons.close, color: AppColors.textSecondary),
      onPressed: () => Navigator.of(context).pop(),
    ),
  ]),
);
```

본문 내 변경 2곳:
- 설명 Text 에 `key: const Key('sheet_description')`, `maxLines: _expanded ? null : 3` (overflow 도 `_expanded ? null : TextOverflow.ellipsis`).
- `_SimilarBooksSection(bookId: book.id)` → `_SimilarBooksSection(bookId: book.id, expanded: _expanded)`. 섹션 내부: `expanded == false` 면 기존 가로 ListView, `true` 면:

```dart
GridView.builder(
  key: const Key('sheet_similar_grid'),
  shrinkWrap: true,
  physics: const NeverScrollableScrollPhysics(),
  padding: const EdgeInsets.symmetric(horizontal: 24),
  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
    crossAxisCount: 2, childAspectRatio: 0.52,
    crossAxisSpacing: 12, mainAxisSpacing: 16),
  itemCount: books.length,
  itemBuilder: (context, index) { /* 기존 _SimilarBookCard 재사용, width 제약 제거 위해 SizedBox 없이 */ },
)
```

주의: `_SimilarBookCard` 는 고정폭 72 — 그리드에선 카드 내부 `SizedBox(width: 72)` 대신 부모 폭을 따르도록 `width` 파라미터를 옵셔널로(기본 72, 그리드에선 null=stretch). 표지 height 는 AspectRatio 로.

- [ ] **Step 4: 통과 확인** — `flutter test test/book_detail_expand_test.dart` → PASS. 이어서 기존 시트 테스트 회귀: `flutter test test/book_detail_test.dart test/shelf_aware_actions_test.dart test/shelf_delete_test.dart` → PASS (Key/구조 변경으로 깨지면 **동작 의미가 같도록** 테스트를 최소 수정).

- [ ] **Step 5: Commit** — `git add lib/features/home/widgets/book_detail_bottom_sheet.dart test/book_detail_expand_test.dart && git commit -m "feat: 책 상세 시트 2단계화 — peek/확장(DraggableScrollableSheet)"`

---

### Task 2: 홈 — 내 책 카드 탭=시트, 롱프레스 제거

**Files:**
- Modify: `lib/features/home/widgets/my_books_section.dart:102-105` (외곽 GestureDetector)
- Modify: `lib/features/home/screens/home_screen.dart:887-890` (_WishlistCard onLongPress 제거)
- Test: `test/my_books_tap_test.dart` (신규)

**Interfaces:**
- Consumes: `BookDetailBottomSheet.show(context, book)` (Task 1, 시그니처 불변).
- 내 책 카드 CTA 버튼(내부 GestureDetector, `_onCtaTap`)은 **변경 금지**.

- [ ] **Step 1: 실패하는 테스트** — `test/my_books_tap_test.dart` (하네스: 기존 홈 위젯 테스트 패턴):

```dart
testWidgets('내 책 카드 몸통 탭 → 상세 시트 오픈', (tester) async {
  await pumpMyBooksSection(tester, readingItem);
  await tester.tap(find.text(readingItem.userBook.book!.title));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
});

testWidgets('CTA 버튼 탭 → 시트 없이 피드백 라우팅 (1탭 저니 회귀 방지)', (tester) async {
  await pumpMyBooksSection(tester, needsFeedbackItem, router: recordingRouter);
  await tester.tap(find.text('피드백 남기기'));
  await tester.pumpAndSettle();
  expect(pushedRoutes.last, startsWith('/feedback/'));
  expect(find.byKey(const Key('sheet_expand_chevron')), findsNothing);
});
```

- [ ] **Step 2: 실패 확인** — `flutter test test/my_books_tap_test.dart` → FAIL (몸통 탭 무동작).

- [ ] **Step 3: 구현** — `_MyBookCard` build 의 외곽 GestureDetector:

```dart
return GestureDetector(
  // 카드 몸통 탭 = 상세 시트 (인터랙션 통일 규칙 1). CTA 버튼은 내부
  // GestureDetector 가 먼저 잡으므로 그대로 직행(규칙 2 — 1탭 피드백 저니).
  onTap: () => BookDetailBottomSheet.show(context, book),
  child: Container( /* 이하 기존 그대로, onLongPress 삭제 */ ),
);
```

`home_screen.dart` `_WishlistCard`: `onLongPress: ...` 줄과 주석 삭제 (onTap 은 이미 시트).

- [ ] **Step 4: 통과 확인** — `flutter test test/my_books_tap_test.dart` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: 홈 내 책 카드 탭=상세 시트, 중복 롱프레스 제거"`

---

### Task 3: 서재 — 책등 탭=시트, 읽는 중 카드 탭=시트

**Files:**
- Modify: `lib/features/library/screens/library_screen.dart:142-144` (onBookTap), `:254-263` (_ReadingCard)
- Test: `test/library_tap_test.dart` (신규)

**Interfaces:**
- Consumes: `BookDetailBottomSheet.show(context, book)`.
- `/book/{userBookId}` 라우트는 삭제하지 않음 — 시트의 [내 평가 보기·수정] 이 진입점(이미 구현됨, ShelfAwareActions.onOpenFeedback).
- `_DoneReadingButton` 은 변경 금지 (1탭 저니).

- [ ] **Step 1: 실패하는 테스트** — `test/library_tap_test.dart`:

```dart
testWidgets('읽은 책 책등 탭 → 페이지 push 대신 상세 시트', (tester) async {
  await pumpLibrary(tester, [readUserBook]);
  await tester.tap(find.byType(BookSpine).first);
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  expect(pushedRoutes.where((r) => r.startsWith('/book/')), isEmpty);
});

testWidgets('읽는 중 카드 몸통 탭 → 상세 시트, 다읽었어요 버튼은 직행', (tester) async {
  await pumpLibrary(tester, [readingUserBook]);
  await tester.tap(find.text(readingUserBook.book!.title));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
});
```

- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** —

```dart
// library_screen.dart onBookTap:
onBookTap: (userBook) {
  BookDetailBottomSheet.show(context, userBook.book!);
},

// _ReadingCard build: Container 를 GestureDetector 로 감싼다.
return GestureDetector(
  onTap: () => BookDetailBottomSheet.show(context, book),
  child: Container( /* 기존 그대로 */ ),
);
```
import 추가: `import '../../home/widgets/book_detail_bottom_sheet.dart';`

- [ ] **Step 4: 통과 확인** — 신규 + `flutter test test/bookshelf_test.dart` 회귀 → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: 서재 책등·읽는중 카드 탭=상세 시트 (페이지는 시트 경유)"`

---

### Task 4: 검색/등록 — 상태선택 시트 폐기, 상세 시트로 통일 (ISBN 매칭)

**Files:**
- Modify: `lib/features/search/screens/book_search_screen.dart` (_showStatusBottomSheet 삭제, 탭 배선)
- Modify: `lib/features/search/widgets/book_search_result_card.dart:19-20` (isAdded 여도 onTap 활성)
- Modify: `lib/features/register/screens/register_flow_screen.dart:241` 부근 (동일 배선)
- Test: `test/book_search_test.dart` (갱신), `test/search_tap_sheet_test.dart` (신규)

**Interfaces:**
- Consumes: `BookDetailBottomSheet.show(context, book)`. 검색 Book 은 `id == ''` — Task 1 의 임프레션 가드가 전제.
- **ISBN 매칭 헬퍼 (Produces):** `Book resolveSheetBook(WidgetRef ref, Book searchBook)` — bookshelfProvider 에서 isbn 일치 UserBook 을 찾으면 그 `book`(실 id, 서재 상태 매칭됨)을, 없으면 검색 Book 그대로 반환. 위치: `lib/features/search/utils/resolve_sheet_book.dart` (신규, 등록 화면과 공유).

- [ ] **Step 1: 실패하는 테스트** — `test/search_tap_sheet_test.dart`:

```dart
test('resolveSheetBook — 서재에 같은 isbn 있으면 실 id Book 반환', () {
  // container 에 bookshelfProvider override (isbn '9791100000001', id 'real-id')
  final resolved = resolveSheetBook(ref, searchBook(isbn: '9791100000001'));
  expect(resolved.id, 'real-id');
});
test('resolveSheetBook — 서재에 없으면 검색 Book 그대로(id 빈값)', () {
  final resolved = resolveSheetBook(ref, searchBook(isbn: '9791100000002'));
  expect(resolved.id, '');
});

testWidgets('기등록 책 탭도 살아있고 상세 시트가 뜬다 (죽은 탭 소멸)', (tester) async {
  await pumpSearchWithResults(tester, added: true);
  await tester.tap(find.text('테스트 책'));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  expect(find.text('읽기 상태 선택'), findsNothing); // 옛 시트 부재
});
```

- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** —

```dart
// lib/features/search/utils/resolve_sheet_book.dart (신규)
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';

/// 검색 결과 Book(id='') 을 시트에 넘기기 전, 서재에 이미 있는 판본이면
/// 실 DB id 를 가진 서재 Book 으로 치환한다 — userBookForProvider 가 id 로
/// 매칭하므로 이 치환이 없으면 기등록 책이 '새 책'으로 보인다.
Book resolveSheetBook(WidgetRef ref, Book searchBook) {
  final isbn = searchBook.isbn;
  if (isbn == null || isbn.isEmpty) return searchBook;
  final shelf = ref.read(bookshelfProvider).valueOrNull ?? const [];
  for (final ub in shelf) {
    if (ub.book?.isbn == isbn) return ub.book!;
  }
  return searchBook;
}
```

`book_search_screen.dart`: `_showStatusBottomSheet` 전체 삭제(등록/스낵바/markAsAdded 포함 — 시트가 등록·스낵바를 담당). 카드 배선:

```dart
BookSearchResultCard(
  book: book,
  isAdded: isAdded,
  onTap: () => BookDetailBottomSheet.show(
    context, resolveSheetBook(ref, book)),
),
```

`book_search_result_card.dart:20`: `onTap: isAdded ? null : onTap` → `onTap: onTap` ('추가됨' 배지는 유지).
`register_flow_screen.dart`: `onTap: isAdded ? null : () => _showStatusBottomSheet(book)` → `onTap: () => BookDetailBottomSheet.show(context, resolveSheetBook(ref, book))`, 같은 파일의 `_showStatusBottomSheet` 삭제.

'추가됨' 배지 최신화: 시트에서 등록하면 `addBookToShelf` 가 bookshelfProvider invalidate → 리스트 빌더의 `isAdded` 계산에 `ref.watch(bookshelfProvider)` isbn 집합을 합집합으로 반영:

```dart
final shelfIsbns = ref.watch(bookshelfProvider).valueOrNull
    ?.map((ub) => ub.book?.isbn).whereType<String>().toSet() ?? const <String>{};
final isAdded = book.isbn != null &&
    (searchState.shelfIsbns.contains(book.isbn) || shelfIsbns.contains(book.isbn));
```

- [ ] **Step 4: 통과 확인** — 신규 + `flutter test test/book_search_test.dart test/book_registration_test.dart` (옛 상태선택 시트를 고정하던 테스트는 새 플로우로 갱신) → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: 검색/등록 탭=상세 시트 통일 — 상태선택 시트 폐기, ISBN 서재 매칭"`

---

### Task 5: 전량 검증 + 저니 회귀 체크

**Files:**
- Test: 전체 스위트.

- [ ] **Step 1:** `cd app && flutter test` → 전량 PASS (실패 시 해당 태스크로 복귀).
- [ ] **Step 2:** 스펙 §5 저니 회귀 체크리스트를 코드 기준으로 재확인하고 결과를 PR 본문에 표로 기록:
  홈→등록 2탭 / 홈→피드백 1탭(버튼) / 서재→다읽었어요 1탭(버튼) / +→등록 3탭(시트 버튼) / 기등록 재탭=시트 / 서가 탭=시트(페이지 push 0) / 평가 수정=시트→[내 평가 보기·수정]→`/book`.
- [ ] **Step 3: Commit & PR** — 브랜치 `feature/unified-book-card-tap`, PR 생성 (머지는 Eden 리뷰 사이클 통과 후).
