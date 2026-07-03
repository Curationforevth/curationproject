import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

/// 서재 상태 인지 액션 영역 — 풀폭 프라이머리 1개 + 균등 아이콘 로우.
/// BookDetailBottomSheet 전체는 Supabase 의존(impression 로그)이라
/// 분리된 공개 위젯(SheetActionArea/ShelfStatusBadge) 단위로 검증한다.

UserBook _ub(BookStatus s, {String? rating}) =>
    UserBook(id: 'ub1', userId: 'u1', bookId: 'b1', status: s, rating: rating);

Widget _wrap(Widget c) => MaterialApp(home: Scaffold(body: c));

SheetActionArea _area(UserBook? ub,
        {VoidCallback? onRevert, VoidCallback? onDelete}) =>
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
  group('ShelfStatusBadge — 상태별 라벨', () {
    testWidgets('찜한 책', (tester) async {
      await tester.pumpWidget(
          _wrap(ShelfStatusBadge(userBook: _ub(BookStatus.wantToRead))));
      expect(find.text('🔖 찜한 책'), findsOneWidget);
    });

    testWidgets('읽는 중', (tester) async {
      await tester.pumpWidget(
          _wrap(ShelfStatusBadge(userBook: _ub(BookStatus.reading))));
      expect(find.text('📖 읽는 중'), findsOneWidget);
    });

    testWidgets('읽은 책 — rating 반영', (tester) async {
      await tester.pumpWidget(_wrap(
          ShelfStatusBadge(userBook: _ub(BookStatus.read, rating: 'good'))));
      expect(find.text('✓ 읽은 책 · 좋았어요'), findsOneWidget);
    });
  });

  group('SheetActionArea — 상태별 프라이머리 + 아이콘 로우', () {
    testWidgets('미보유 — 프라이머리 읽었어요 + 아이콘 로우 3개', (t) async {
      await t.pumpWidget(_wrap(_area(null)));
      expect(find.text('읽었어요'), findsOneWidget);
      expect(find.text('읽는 중'), findsOneWidget);
      expect(find.text('읽고싶어요'), findsOneWidget);
      expect(find.text('관심 없어요'), findsOneWidget);
    });

    testWidgets('찜한 책 — 읽었어요 + 읽는중·찜 해제', (t) async {
      await t.pumpWidget(_wrap(_area(_ub(BookStatus.wantToRead))));
      expect(find.text('읽었어요'), findsOneWidget);
      expect(find.text('찜 해제'), findsOneWidget);
    });

    testWidgets('읽는 중 — 다 읽었어요 + 삭제만', (t) async {
      await t.pumpWidget(_wrap(_area(_ub(BookStatus.reading))));
      expect(find.text('다 읽었어요'), findsOneWidget);
      expect(find.text('삭제'), findsOneWidget);
      expect(find.text('읽고싶어요'), findsNothing);
    });

    testWidgets('읽은 책 평가無 — 평가 남기기 + 되돌리기·삭제, 되돌리기 콜백', (t) async {
      var revert = 0;
      await t.pumpWidget(
          _wrap(_area(_ub(BookStatus.read), onRevert: () => revert++)));
      expect(find.text('평가 남기기'), findsOneWidget);
      expect(find.text('되돌리기'), findsOneWidget);
      await t.tap(find.text('되돌리기'));
      expect(revert, 1);
    });

    testWidgets('읽은 책 평가有 — 프라이머리 없음(카드 대체) + 되돌리기·삭제', (t) async {
      await t.pumpWidget(_wrap(_area(_ub(BookStatus.read, rating: 'good'))));
      expect(find.text('평가 남기기'), findsNothing);
      expect(find.text('되돌리기'), findsOneWidget);
      expect(find.text('삭제'), findsOneWidget);
    });

    testWidgets('삭제 아이콘 탭 → onDelete 콜백', (t) async {
      var del = 0;
      await t.pumpWidget(
          _wrap(_area(_ub(BookStatus.reading), onDelete: () => del++)));
      await t.tap(find.text('삭제'));
      expect(del, 1);
    });
  });

  group('userBookForProvider — 서재 조회(네트워크 0)', () {
    test('로드된 서재에서 book_id 매칭, 없으면/로딩 중이면 null', () async {
      final shelf = [_ub(BookStatus.reading)];
      final container = ProviderContainer(overrides: [
        bookshelfProvider.overrideWith((ref) async => shelf),
      ]);
      addTearDown(container.dispose);

      expect(container.read(userBookForProvider('b1')), isNull);
      await container.read(bookshelfProvider.future);

      expect(container.read(userBookForProvider('b1'))?.status,
          BookStatus.reading);
      expect(container.read(userBookForProvider('없는책')), isNull);
    });
  });
}
