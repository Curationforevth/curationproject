# 책 상세 시트 · 액션 영역 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 책 상세 바텀시트의 비대칭 4버튼 산개 액션 영역을 "풀폭 프라이머리 1개 + 균등 아이콘 액션 로우(뮤트)"로 통일한다.

**Architecture:** 상태→액션을 순수 매핑 함수 `actionsForState()` 로 분리(테스트 타깃)하고, 단일 `SheetActionArea` 위젯이 그 결과를 렌더. 기존 `ShelfAwareActions`/`NotInterestedAction`/`ShelfDeleteAction` 3개와 하단 destructive Padding 블록을 대체·제거. 신규 "되돌리기"(read→reading + 평가 삭제)는 기존 `removeFromShelfWith` 패턴(순수 payload + thin client wrapper + container wrapper)을 그대로 따른다.

**Tech Stack:** Flutter, Riverpod, Supabase, flutter_test.

## Global Constraints

- 서재 상태값: read→`'finished'`, reading→`'reading'`, wantToRead→`'wishlist'` (UserBook.status.toJson()).
- 되돌리기는 신규 DB write 경로 → **prod E2E throwaway 실쓰기 검증 필수**(CLAUDE.md, pm-agent `ref_prod_e2e_throwaway`). dry-run 은 CHECK/트리거 못 잡음.
- 톤(뮤트 확정): neutral icon `Color(0xFF334155)` / label `Color(0xFF475569)`; destructive icon+label `Color(0xFF8A94A6)`.
- 프라이머리: bg `AppColors.primary`, text `AppColors.textOnPrimary`, height 52, radius 12, font 15/w600.
- 읽는 중 로우 = `삭제`만(읽고싶어요 제외 — 데이터 계층이 wishlist 강등 금지).
- 되돌리기 = 실행취소 없음(스낵바 안내만). 이전 평가는 복구 안 됨.
- CODE_REV 범프는 이 PR엔 불필요(서버 무변경, 앱만).
- 폰 재빌드/설치는 오늘(7/3) 완료된 상태에서 추가 1회 필요([[project-ios-free-provisioning-7day]], 만료 7/10).

---

### Task 1: 되돌리기 데이터 경로 (read→reading + 평가 삭제)

**Files:**
- Modify: `app/lib/features/bookshelf/providers/bookshelf_provider.dart` (removeFromShelf 계열 아래에 추가)
- Test: `app/test/revert_to_reading_test.dart` (Create)

**Interfaces:**
- Produces:
  - `Map<String, dynamic> revertToReadingPayload()` — `{'status':'reading','rating':null,'emotion_tags':null,'review_text':null}` (순수).
  - `Future<void> revertToReadingWith(SupabaseClient client, UserBook userBook)` — user_books UPDATE.
  - `Future<void> revertToReading(ProviderContainer container, UserBook userBook)` — invalidate + recompute + 지연 재조회.

- [ ] **Step 1: 실패하는 테스트 작성** — payload 순수함수

`app/test/revert_to_reading_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';

void main() {
  test('revertToReadingPayload — status reading + 평가필드 전부 null', () {
    final p = revertToReadingPayload();
    expect(p['status'], 'reading');
    expect(p.containsKey('rating'), isTrue);
    expect(p['rating'], isNull);
    expect(p['emotion_tags'], isNull);
    expect(p['review_text'], isNull);
  });
}
```

- [ ] **Step 2: 실패 확인**

Run: `cd app && flutter test test/revert_to_reading_test.dart`
Expected: FAIL — `revertToReadingPayload` 미정의 컴파일 에러.

- [ ] **Step 3: 구현 추가** (bookshelf_provider.dart, restoreToShelfWith 아래)

```dart
/// 읽은 책을 "읽는 중"으로 되돌린다 — 평가(rating/감정태그/감상)는 삭제한다.
/// DB CHECK("rating 은 finished 에만")를 지키고, 의미상 완독 평가가 소멸하므로
/// 세 필드를 명시적으로 null 로 UPDATE 한다. addBookToShelf(reading) 재사용
/// 불가: resolveShelfWrite 가 rating 을 보존해 되돌려도 평가가 남는다.
Map<String, dynamic> revertToReadingPayload() => {
      'status': BookStatus.reading.toJson(),
      'rating': null,
      'emotion_tags': null,
      'review_text': null,
    };

/// Supabase 호출부만 분리 — 테스트 fake client 주입용(removeFromShelfWith 패턴).
Future<void> revertToReadingWith(
  SupabaseClient client,
  UserBook userBook,
) async {
  await client
      .from('user_books')
      .update(revertToReadingPayload())
      .eq('id', userBook.id);
}

/// 되돌리기 커밋 + 서재 무효화 + 취향 재계산 트리거(read+rating 제거는 스코어링
/// 입력 변화). removeFromShelf 와 동일한 container/지연 invalidate 패턴.
Future<void> revertToReading(
  ProviderContainer container,
  UserBook userBook,
) async {
  final supabase = Supabase.instance.client;
  await revertToReadingWith(supabase, userBook);
  container.invalidate(bookshelfProvider);
  unawaited(container.read(recommendationServiceProvider).triggerRecompute());
  unawaited(
    Future<void>.delayed(const Duration(seconds: 2)).then((_) {
      container.invalidate(recommendationsProvider);
    }),
  );
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd app && flutter test test/revert_to_reading_test.dart`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add app/lib/features/bookshelf/providers/bookshelf_provider.dart app/test/revert_to_reading_test.dart
git commit -m "feat: 되돌리기 데이터 경로(read→reading, 평가 삭제) + payload 테스트"
```

---

### Task 2: actionsForState 순수 매핑 + 데이터 모델

**Files:**
- Modify: `app/lib/features/home/widgets/book_detail_bottom_sheet.dart` (상단, BookDetailBottomSheet 위 또는 파일 하단 유틸 영역)
- Test: `app/test/sheet_action_area_test.dart` (Create)

**Interfaces:**
- Produces:
  - `enum SheetActionTone { neutral, danger }`
  - `class SheetAction { final IconData icon; final String label; final VoidCallback onTap; final SheetActionTone tone; }`
  - `class SheetActionSpec { final String? primaryLabel; final VoidCallback? onPrimary; final bool showRatingCard; final List<SheetAction> row; }`
  - `SheetActionSpec actionsForState({required UserBook? userBook, required VoidCallback onReading, required VoidCallback onRead, required VoidCallback onBookmark, required VoidCallback onNotInterested, required VoidCallback onDelete, required VoidCallback onRevert, required VoidCallback onOpenFeedback})`

- [ ] **Step 1: 실패하는 테스트 작성**

`app/test/sheet_action_area_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

UserBook _ub(BookStatus s, {String? rating}) => UserBook(
    id: 'ub1', userId: 'u1', bookId: 'b1', status: s, rating: rating);

SheetActionSpec _spec(UserBook? ub, Map<String, int> hits) => actionsForState(
      userBook: ub,
      onReading: () => hits['reading'] = (hits['reading'] ?? 0) + 1,
      onRead: () => hits['read'] = (hits['read'] ?? 0) + 1,
      onBookmark: () => hits['bookmark'] = (hits['bookmark'] ?? 0) + 1,
      onNotInterested: () => hits['ni'] = (hits['ni'] ?? 0) + 1,
      onDelete: () => hits['delete'] = (hits['delete'] ?? 0) + 1,
      onRevert: () => hits['revert'] = (hits['revert'] ?? 0) + 1,
      onOpenFeedback: () => hits['feedback'] = (hits['feedback'] ?? 0) + 1,
    );

void main() {
  test('미보유 — 프라이머리 읽었어요 + 로우 3개(읽는중·읽고싶어요·관심없어요[danger])', () {
    final h = <String, int>{};
    final s = _spec(null, h);
    expect(s.primaryLabel, '읽었어요');
    expect(s.showRatingCard, isFalse);
    expect(s.row.map((a) => a.label).toList(),
        ['읽는 중', '읽고싶어요', '관심 없어요']);
    expect(s.row.last.tone, SheetActionTone.danger);
    s.onPrimary!();
    expect(h['read'], 1);
    s.row.last.onTap();
    expect(h['ni'], 1);
  });

  test('찜 — 프라이머리 읽었어요 + 로우(읽는중·찜 해제)', () {
    final s = _spec(_ub(BookStatus.wantToRead), {});
    expect(s.primaryLabel, '읽었어요');
    expect(s.row.map((a) => a.label).toList(), ['읽는 중', '찜 해제']);
    expect(s.row.every((a) => a.tone == SheetActionTone.neutral), isTrue);
  });

  test('읽는 중 — 프라이머리 다 읽었어요 + 로우(삭제[danger]만)', () {
    final s = _spec(_ub(BookStatus.reading), {});
    expect(s.primaryLabel, '다 읽었어요');
    expect(s.row.map((a) => a.label).toList(), ['삭제']);
    expect(s.row.single.tone, SheetActionTone.danger);
  });

  test('읽은 책 평가無 — 프라이머리 평가 남기기 + 로우(되돌리기·삭제)', () {
    final h = <String, int>{};
    final s = _spec(_ub(BookStatus.read), h);
    expect(s.primaryLabel, '평가 남기기');
    expect(s.showRatingCard, isFalse);
    expect(s.row.map((a) => a.label).toList(), ['되돌리기', '삭제']);
    s.row.first.onTap();
    expect(h['revert'], 1);
    s.onPrimary!();
    expect(h['feedback'], 1);
  });

  test('읽은 책 평가有 — 카드 노출(primary 없음) + 로우(되돌리기·삭제)', () {
    final s = _spec(_ub(BookStatus.read, rating: 'good'), {});
    expect(s.showRatingCard, isTrue);
    expect(s.primaryLabel, isNull);
    expect(s.onPrimary, isNull);
    expect(s.row.map((a) => a.label).toList(), ['되돌리기', '삭제']);
  });
}
```

- [ ] **Step 2: 실패 확인**

Run: `cd app && flutter test test/sheet_action_area_test.dart`
Expected: FAIL — `actionsForState`/`SheetActionSpec` 미정의.

- [ ] **Step 3: 모델 + 매핑 구현** (book_detail_bottom_sheet.dart 하단 유틸 영역에 추가)

```dart
enum SheetActionTone { neutral, danger }

class SheetAction {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final SheetActionTone tone;
  const SheetAction(this.icon, this.label, this.onTap,
      {this.tone = SheetActionTone.neutral});
}

/// 프라이머리 슬롯 + 아이콘 로우를 상태로부터 순수하게 파생.
/// showRatingCard 면 primary 슬롯 대신 MyRatingSection 을 렌더한다.
class SheetActionSpec {
  final String? primaryLabel;
  final VoidCallback? onPrimary;
  final bool showRatingCard;
  final List<SheetAction> row;
  const SheetActionSpec({
    this.primaryLabel,
    this.onPrimary,
    this.showRatingCard = false,
    required this.row,
  });
}

SheetActionSpec actionsForState({
  required UserBook? userBook,
  required VoidCallback onReading,
  required VoidCallback onRead,
  required VoidCallback onBookmark,
  required VoidCallback onNotInterested,
  required VoidCallback onDelete,
  required VoidCallback onRevert,
  required VoidCallback onOpenFeedback,
}) {
  switch (userBook?.status) {
    case null:
      return SheetActionSpec(primaryLabel: '읽었어요', onPrimary: onRead, row: [
        SheetAction(Icons.menu_book_outlined, '읽는 중', onReading),
        SheetAction(Icons.bookmark_border, '읽고싶어요', onBookmark),
        SheetAction(Icons.visibility_off_outlined, '관심 없어요', onNotInterested,
            tone: SheetActionTone.danger),
      ]);
    case BookStatus.wantToRead:
      return SheetActionSpec(primaryLabel: '읽었어요', onPrimary: onRead, row: [
        SheetAction(Icons.menu_book_outlined, '읽는 중', onReading),
        SheetAction(Icons.bookmark_remove_outlined, '찜 해제', onDelete),
      ]);
    case BookStatus.reading:
      return SheetActionSpec(primaryLabel: '다 읽었어요', onPrimary: onRead, row: [
        SheetAction(Icons.delete_outline, '삭제', onDelete,
            tone: SheetActionTone.danger),
      ]);
    case BookStatus.read:
      final rated = userBook!.rating != null;
      return SheetActionSpec(
        primaryLabel: rated ? null : '평가 남기기',
        onPrimary: rated ? null : onOpenFeedback,
        showRatingCard: rated,
        row: [
          SheetAction(Icons.undo, '되돌리기', onRevert),
          SheetAction(Icons.delete_outline, '삭제', onDelete,
              tone: SheetActionTone.danger),
        ],
      );
  }
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd app && flutter test test/sheet_action_area_test.dart`
Expected: PASS (5 tests).

- [ ] **Step 5: 커밋**

```bash
git add app/lib/features/home/widgets/book_detail_bottom_sheet.dart app/test/sheet_action_area_test.dart
git commit -m "feat: 시트 액션 상태 매핑 actionsForState + 데이터 모델(순수, 5상태 테스트)"
```

---

### Task 3: SheetActionArea + IconActionRow 위젯, 시트 통합, 구 위젯 제거

**Files:**
- Modify: `app/lib/features/home/widgets/book_detail_bottom_sheet.dart`
- Test: `app/test/shelf_aware_actions_test.dart` (기존 → 재작성)

**Interfaces:**
- Consumes: Task 1 `revertToReading`, Task 2 `actionsForState`/`SheetActionSpec`/`SheetAction`/`SheetActionTone`.
- Produces: `class SheetActionArea extends StatelessWidget`(userBook, isLoading, 콜백들), `class IconActionRow`(actions).

- [ ] **Step 1: 위젯 테스트 재작성** (shelf_aware_actions_test.dart 전체 교체 — ShelfAwareActions 삭제되므로)

`app/test/shelf_aware_actions_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

UserBook _ub(BookStatus s, {String? rating}) => UserBook(
    id: 'ub1', userId: 'u1', bookId: 'b1', status: s, rating: rating);

Widget _wrap(Widget c) => MaterialApp(home: Scaffold(body: c));

SheetActionArea _area(UserBook? ub, {VoidCallback? onRevert, VoidCallback? onDelete}) =>
    SheetActionArea(
      userBook: ub,
      isLoading: false,
      onReading: () {},
      onRead: () {},
      onBookmark: () {},
      onNotInterested: () {},
      onDelete: onDelete ?? () {},
      onRevert: onRevert ?? () {},
      onOpenFeedback: () {},
    );

void main() {
  testWidgets('미보유 — 프라이머리 읽었어요 + 아이콘 로우 3개', (t) async {
    await t.pumpWidget(_wrap(_area(null)));
    expect(find.text('읽었어요'), findsOneWidget);
    expect(find.text('읽는 중'), findsOneWidget);
    expect(find.text('읽고싶어요'), findsOneWidget);
    expect(find.text('관심 없어요'), findsOneWidget);
  });

  testWidgets('읽는 중 — 다 읽었어요 + 삭제만', (t) async {
    await t.pumpWidget(_wrap(_area(_ub(BookStatus.reading))));
    expect(find.text('다 읽었어요'), findsOneWidget);
    expect(find.text('삭제'), findsOneWidget);
    expect(find.text('읽고싶어요'), findsNothing);
  });

  testWidgets('읽은 책 평가無 — 평가 남기기 + 되돌리기·삭제, 콜백 연결', (t) async {
    var revert = 0;
    await t.pumpWidget(_wrap(_area(_ub(BookStatus.read), onRevert: () => revert++)));
    expect(find.text('평가 남기기'), findsOneWidget);
    expect(find.text('되돌리기'), findsOneWidget);
    await t.tap(find.text('되돌리기'));
    expect(revert, 1);
  });

  testWidgets('읽은 책 평가有 — 내 평가 카드 + 되돌리기·삭제(프라이머리 없음)', (t) async {
    await t.pumpWidget(_wrap(_area(_ub(BookStatus.read, rating: 'good'))));
    expect(find.text('내 평가'), findsOneWidget);
    expect(find.text('평가 남기기'), findsNothing);
    expect(find.text('되돌리기'), findsOneWidget);
    expect(find.text('삭제'), findsOneWidget);
  });
}
```

- [ ] **Step 2: 실패 확인**

Run: `cd app && flutter test test/shelf_aware_actions_test.dart`
Expected: FAIL — `SheetActionArea` 미정의 + 구 `ShelfAwareActions` 참조 제거로 컴파일 에러.

- [ ] **Step 3: SheetActionArea + IconActionRow 구현** (book_detail_bottom_sheet.dart)

`_ActionButton`(프라이머리 재사용)은 유지. 신규 위젯 추가:
```dart
/// 상태→액션 통합 렌더: 프라이머리(또는 내 평가 카드) + 균등 아이콘 로우.
class SheetActionArea extends StatelessWidget {
  final UserBook? userBook;
  final bool isLoading;
  final VoidCallback onReading, onRead, onBookmark;
  final VoidCallback onNotInterested, onDelete, onRevert, onOpenFeedback;
  const SheetActionArea({
    super.key,
    required this.userBook,
    required this.isLoading,
    required this.onReading,
    required this.onRead,
    required this.onBookmark,
    required this.onNotInterested,
    required this.onDelete,
    required this.onRevert,
    required this.onOpenFeedback,
  });

  @override
  Widget build(BuildContext context) {
    final spec = actionsForState(
      userBook: userBook,
      onReading: onReading,
      onRead: onRead,
      onBookmark: onBookmark,
      onNotInterested: onNotInterested,
      onDelete: onDelete,
      onRevert: onRevert,
      onOpenFeedback: onOpenFeedback,
    );
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (!spec.showRatingCard && spec.primaryLabel != null)
          _ActionButton(
            label: spec.primaryLabel!,
            isPrimary: true,
            isLoading: isLoading,
            onTap: spec.onPrimary!,
          ),
        if (spec.row.isNotEmpty) ...[
          const SizedBox(height: 10),
          IconActionRow(actions: spec.row, isLoading: isLoading),
        ],
      ],
    );
  }
}

/// 균등 아이콘 액션 로우 — 세로 스택(아이콘 23 + 라벨 11), flex:1, 무테두리.
class IconActionRow extends StatelessWidget {
  final List<SheetAction> actions;
  final bool isLoading;
  const IconActionRow({super.key, required this.actions, this.isLoading = false});

  static const _neutralIcon = Color(0xFF334155);
  static const _neutralLabel = Color(0xFF475569);
  static const _danger = Color(0xFF8A94A6);

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (final a in actions)
          Expanded(
            child: Semantics(
              button: true,
              label: a.label,
              child: InkWell(
                onTap: isLoading ? null : a.onTap,
                borderRadius: BorderRadius.circular(12),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 2),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(a.icon,
                          size: 23,
                          color: a.tone == SheetActionTone.danger
                              ? _danger
                              : _neutralIcon),
                      const SizedBox(height: 6),
                      Text(a.label,
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w500,
                            color: a.tone == SheetActionTone.danger
                                ? _danger
                                : _neutralLabel,
                          )),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
```

- [ ] **Step 4: 시트 build() 통합 + 구 위젯/블록 제거**

`_BookDetailBottomSheetState.build()` 의 액션 영역(현재 372~415행: `if (userBook?.status == read && rating != null) MyRatingSection ... else Padding(ShelfAwareActions) ...` + 그 아래 destructive `Padding(... ShelfDeleteAction/NotInterestedAction ...)`)을 아래로 교체:

```dart
// 평가有 읽은 책은 내 평가 카드가 프라이머리 슬롯을 대신한다.
if (userBook?.status == BookStatus.read && userBook!.rating != null)
  MyRatingSection(
    userBook: userBook,
    onEdit: () {
      Navigator.of(context).pop();
      context.push('/feedback/${userBook.id}');
    },
  ),
Padding(
  padding: const EdgeInsets.symmetric(horizontal: 24),
  child: SheetActionArea(
    userBook: userBook,
    isLoading: _isLoading,
    onReading: _handleReading,
    onRead: _handleRead,
    onBookmark: _handleBookmark,
    onNotInterested: () => unawaited(_handleNotInterested(book)),
    onDelete: () => unawaited(_handleDelete(userBook!)),
    onRevert: () => unawaited(_handleRevertToReading(userBook!)),
    onOpenFeedback: () {
      if (userBook != null) {
        Navigator.of(context).pop();
        context.push('/feedback/${userBook.id}');
      }
    },
  ),
),
```

그리고 클래스 `ShelfAwareActions`, `NotInterestedAction`, `ShelfDeleteAction` 정의 **삭제**(더 이상 참조 없음). `_BookmarkButton` 도 ShelfAwareActions 전용이었으면 삭제. `MyRatingSection`/`_ActionButton`/`ShelfStatusBadge`/`showTimedSnackBar`/`showDeletedSnackBar` 는 유지.

신규 핸들러 추가(_handleDelete 아래):
```dart
/// 읽은 책 → 읽는 중 되돌리기(평가 삭제). 실행취소 없음 — 스낵바 안내만.
Future<void> _handleRevertToReading(UserBook userBook) async {
  final navigator = Navigator.of(context);
  final rootContext = navigator.context;
  final container = ProviderScope.containerOf(context, listen: false);
  navigator.pop();
  await revertToReading(container, userBook);
  if (rootContext.mounted) {
    showTimedSnackBar(
      rootContext,
      const SnackBar(content: Text('읽는 중으로 되돌렸어요')),
    );
  }
}
```

- [ ] **Step 5: 통과 확인 + 전체 컴파일**

Run: `cd app && flutter test test/shelf_aware_actions_test.dart && flutter analyze`
Expected: 위젯 테스트 PASS, analyze 에서 미사용 심볼/미참조 없음(구 위젯 삭제 확인).

- [ ] **Step 6: 커밋**

```bash
git add app/lib/features/home/widgets/book_detail_bottom_sheet.dart app/test/shelf_aware_actions_test.dart
git commit -m "feat: SheetActionArea+IconActionRow 통합, 구 3위젯·산개 destructive 제거"
```

---

### Task 4: '읽은 책' 배지 탭 되돌리기 + 잔여 테스트 갱신

**Files:**
- Modify: `app/lib/features/home/widgets/book_detail_bottom_sheet.dart` (ShelfStatusBadge + build 의 배지 사용부)
- Test: `app/test/book_my_rating_test.dart`, `app/test/book_detail_expand_test.dart` (필요 시 셀렉터 갱신)

**Interfaces:**
- Consumes: Task 3 `_handleRevertToReading`.
- Produces: `ShelfStatusBadge` 에 `VoidCallback? onRevert` 추가(read 상태일 때만 탭 가능).

- [ ] **Step 1: 배지 탭 테스트 추가** (shelf_aware_actions_test.dart 에 그룹 추가 또는 book_my_rating_test 에)

```dart
testWidgets('ShelfStatusBadge — 읽은 책이면 탭으로 되돌리기 콜백', (t) async {
  var revert = 0;
  await t.pumpWidget(MaterialApp(home: Scaffold(body:
    ShelfStatusBadge(
      userBook: UserBook(id:'ub1',userId:'u1',bookId:'b1',
        status: BookStatus.read, rating: 'good'),
      onRevert: () => revert++,
    ))));
  await t.tap(find.text('✓ 읽은 책 · 좋았어요'));
  expect(revert, 1);
});

testWidgets('ShelfStatusBadge — 찜한 책은 탭 불가(onRevert null)', (t) async {
  await t.pumpWidget(MaterialApp(home: Scaffold(body:
    ShelfStatusBadge(
      userBook: UserBook(id:'ub1',userId:'u1',bookId:'b1',
        status: BookStatus.wantToRead),
    ))));
  // 탭해도 예외 없음(비인터랙티브)
  await t.tap(find.text('🔖 찜한 책'));
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd app && flutter test test/shelf_aware_actions_test.dart`
Expected: FAIL — `ShelfStatusBadge` 에 `onRevert` 파라미터 없음.

- [ ] **Step 3: ShelfStatusBadge 에 onRevert 추가**

```dart
class ShelfStatusBadge extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback? onRevert; // read 상태일 때만 배지 탭 = 되돌리기(보조 경로)
  const ShelfStatusBadge({super.key, required this.userBook, this.onRevert});
  // ... _label 동일 ...
  @override
  Widget build(BuildContext context) {
    final badge = Container( /* 기존 Container 그대로 */ );
    if (onRevert == null) return Semantics(label: '서재 상태: $_label', child: badge);
    return Semantics(
      button: true,
      label: '서재 상태: $_label. 탭하면 읽는 중으로 되돌립니다',
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onRevert,
        child: badge,
      ),
    );
  }
}
```

build() 의 배지 사용부(현재 336행 `ShelfStatusBadge(userBook: userBook)`)를 교체:
```dart
ShelfStatusBadge(
  userBook: userBook,
  onRevert: userBook.status == BookStatus.read
      ? () => unawaited(_handleRevertToReading(userBook))
      : null,
),
```

- [ ] **Step 4: 통과 + 잔여 테스트 갱신**

Run: `cd app && flutter test test/shelf_aware_actions_test.dart test/book_my_rating_test.dart test/book_detail_expand_test.dart`
Expected: PASS. 실패하면 각 테스트의 셀렉터를 새 위젯 트리(SheetActionArea/IconActionRow, '되돌리기' 텍스트)에 맞춰 갱신 — 텍스트 기반 finder 위주라 대개 그대로 통과.

- [ ] **Step 5: 커밋**

```bash
git add app/lib/features/home/widgets/book_detail_bottom_sheet.dart app/test/
git commit -m "feat: 읽은 책 배지 탭 되돌리기(보조 경로) + 잔여 테스트 갱신"
```

---

### Task 5: 전체 검증 (테스트 그린 + prod 실쓰기 + 실기기)

**Files:** (코드 변경 없음 — 검증)

- [ ] **Step 1: 앱 전체 테스트**

Run: `cd app && flutter test`
Expected: 전 스위트 PASS(신규 2파일 + 갱신 스위트 포함).

- [ ] **Step 2: prod E2E throwaway — 되돌리기 실쓰기 검증** (CLAUDE.md 필수)

pm-agent `ref_prod_e2e_throwaway` 절차로:
- admin API 로 throwaway 유저 생성 → user_books 에 `status='finished', rating='good', emotion_tags=['몰입감'], review_text='x'` 시딩.
- `revertToReadingWith` 동등 UPDATE(REST 또는 로컬 TestClient) 실행.
- 확인: 해당 행이 `status='reading'` & `rating/emotion_tags/review_text` 전부 NULL. CHECK 위반 없이 커밋. user_state 재계산 트리거(트리거 존재 시) 관측.
- 정리: user_books/recommendation_cache/user_state/auth user 삭제.

Expected: 실 DB 에서 CHECK/트리거 위반 없이 read→reading + 평가 null 확정.

- [ ] **Step 3: 실기기 스크린샷 5상태**

폰 재빌드·설치([[project-ios-free-provisioning-7day]] 절차) 후 시트 열어 미보유/찜/읽는중/읽은책(평가無)/읽은책(평가有) 액션 영역 + 되돌리기(배지 탭·로우 아이콘) 확인. Eden 스크린샷 공유.

- [ ] **Step 4: 최종 커밋/PR**

```bash
git add -A
git commit -m "test: 시트 액션 재설계 검증(전체 테스트 그린 + prod 되돌리기 실쓰기)"
```
PR 생성(push 전 `gh api user --jq .login` 이 hyhuh0910 확인, [[feedback-git-push]]).

---

## Self-Review

**Spec coverage:** 매트릭스 5상태(Task 2/3) · 되돌리기 데이터경로+평가삭제(Task 1) · 배지 탭(Task 4) · 구 3위젯 제거+산개 소멸(Task 3) · 톤 뮤트값(Task 3 IconActionRow 상수) · prod 실쓰기+실기기(Task 5) 전부 커버. 읽는 중 읽고싶어요 제외(Global Constraints + Task 2 로우) 반영.

**Placeholder scan:** 모든 코드 스텝에 실제 코드 포함. TODO/TBD 없음.

**Type consistency:** `actionsForState`(Task 2) 시그니처 = `SheetActionArea`(Task 3) 호출 인자 일치. `revertToReading(container, userBook)`(Task 1) = `_handleRevertToReading`(Task 3) 호출 일치. `SheetAction`/`SheetActionTone`/`SheetActionSpec` 전 태스크 동일 명명.
