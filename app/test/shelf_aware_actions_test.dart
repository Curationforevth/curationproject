import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

/// 서재 상태 인지 UI — 상태별 배지/버튼 분기 (Goodreads 패턴).
/// BookDetailBottomSheet 전체는 Supabase 의존(impression 로그)이라
/// 기존 테스트 관례대로 분리된 공개 위젯 + provider 단위로 검증한다.

UserBook _ub(BookStatus status, {String? rating, String bookId = 'b1'}) =>
    UserBook(
      id: 'ub-$bookId',
      userId: 'u1',
      bookId: bookId,
      status: status,
      rating: rating,
    );

Widget _wrap(Widget child) =>
    MaterialApp(home: Scaffold(body: Center(child: child)));

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

      await tester.pumpWidget(
          _wrap(ShelfStatusBadge(userBook: _ub(BookStatus.read))));
      expect(find.text('✓ 읽은 책'), findsOneWidget);
    });
  });

  group('ShelfAwareActions — 상태별 버튼 분기', () {
    Widget actions(UserBook? ub,
        {VoidCallback? onRead, VoidCallback? onOpenFeedback}) {
      return _wrap(ShelfAwareActions(
        userBook: ub,
        isLoading: false,
        bookmarked: ub?.status == BookStatus.wantToRead,
        onReading: () {},
        onRead: onRead ?? () {},
        onBookmark: () {},
        onOpenFeedback: onOpenFeedback ?? () {},
      ));
    }

    testWidgets('서재에 없음 — 새 책 3버튼', (tester) async {
      await tester.pumpWidget(actions(null));
      expect(find.text('읽는 중'), findsOneWidget);
      expect(find.text('읽었어요'), findsOneWidget);
      expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
    });

    testWidgets('찜한 책 — 3버튼 유지 + 북마크 filled', (tester) async {
      await tester.pumpWidget(actions(_ub(BookStatus.wantToRead)));
      expect(find.text('읽었어요'), findsOneWidget);
      expect(find.byIcon(Icons.bookmark), findsOneWidget);
    });

    testWidgets('읽는 중 — [다 읽었어요] 단독, onRead 연결', (tester) async {
      var readTapped = false;
      await tester.pumpWidget(
          actions(_ub(BookStatus.reading), onRead: () => readTapped = true));
      expect(find.text('다 읽었어요'), findsOneWidget);
      expect(find.text('읽는 중'), findsNothing);
      expect(find.byIcon(Icons.bookmark_border), findsNothing);
      await tester.tap(find.text('다 읽었어요'));
      expect(readTapped, isTrue);
    });

    testWidgets('읽은 책(평가 있음) — [내 평가 보기 · 수정] → 피드백', (tester) async {
      var feedbackTapped = false;
      await tester.pumpWidget(actions(_ub(BookStatus.read, rating: 'good'),
          onOpenFeedback: () => feedbackTapped = true));
      expect(find.text('내 평가 보기 · 수정'), findsOneWidget);
      await tester.tap(find.text('내 평가 보기 · 수정'));
      expect(feedbackTapped, isTrue);
    });

    testWidgets('읽은 책(평가 없음) — [평가 남기기]', (tester) async {
      await tester.pumpWidget(actions(_ub(BookStatus.read)));
      expect(find.text('평가 남기기'), findsOneWidget);
      expect(find.text('읽었어요'), findsNothing);
    });
  });

  group('userBookForProvider — 서재 조회(네트워크 0)', () {
    test('로드된 서재에서 book_id 매칭, 없으면/로딩 중이면 null', () async {
      final shelf = [_ub(BookStatus.reading, bookId: 'b1')];
      final container = ProviderContainer(overrides: [
        bookshelfProvider.overrideWith((ref) async => shelf),
      ]);
      addTearDown(container.dispose);

      // 로딩 중 → null (새 책 UI 폴백)
      expect(container.read(userBookForProvider('b1')), isNull);
      await container.read(bookshelfProvider.future);

      expect(container.read(userBookForProvider('b1'))?.status,
          BookStatus.reading);
      expect(container.read(userBookForProvider('없는책')), isNull);
    });
  });
}
