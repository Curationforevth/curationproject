import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/impression_logger.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/home_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/home/widgets/my_books_section.dart';

/// 홈 "내 책" 카드 — 몸통 탭=상세 시트, CTA 버튼 탭=기존 1탭 피드백 저니 유지.
///
/// 하네스는 book_detail_expand_test.dart 패턴 재사용:
/// impressionLoggerProvider no-op 페이크 + bookshelfProvider/similarBooksProvider
/// override. 라우팅 검증용으로 pushedRoutes 를 기록하는 커스텀 GoRouter 를 추가한다.

class _FakeImpressionLogger extends ImpressionLogger {
  _FakeImpressionLogger()
      : super(SupabaseClient(
          'http://localhost',
          'anon-key',
          authOptions: const AuthClientOptions(autoRefreshToken: false),
        ));

  @override
  Future<void> logAction({required String bookId, required String action}) async {
    // no-op
  }

  @override
  Future<void> logImpressions({
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) async {
    // no-op
  }
}

final _readingBook = const Book(id: 'b-reading', title: '읽는 중인 책', author: '저자A');
final _needsFeedbackBook =
    const Book(id: 'b-feedback', title: '피드백 대기 책', author: '저자B');

final _readingUserBook = UserBook(
  id: 'ub-reading',
  userId: 'u1',
  bookId: 'b-reading',
  status: BookStatus.reading,
  book: _readingBook,
);

final _needsFeedbackUserBook = UserBook(
  id: 'ub-feedback',
  userId: 'u1',
  bookId: 'b-feedback',
  status: BookStatus.read,
  book: _needsFeedbackBook,
);

Future<List<String>> pumpMyBooksSection(
  WidgetTester tester,
  ({UserBook userBook, String ctaType}) item, {
  List<String>? pushedRoutes,
}) async {
  final routes = pushedRoutes ?? <String>[];
  final router = GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const Scaffold(body: MyBooksSection()),
      ),
      GoRoute(
        path: '/feedback/:id',
        builder: (context, state) {
          routes.add(state.uri.toString());
          return const Scaffold(body: Text('feedback screen'));
        },
      ),
    ],
  );

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        impressionLoggerProvider.overrideWithValue(_FakeImpressionLogger()),
        bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
        similarBooksProvider(item.userBook.book!.id)
            .overrideWith((ref) async => const []),
        myBooksProvider.overrideWith((ref) => [item]),
      ],
      child: MaterialApp.router(routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return routes;
}

void main() {
  testWidgets('내 책 카드 몸통 탭 → 상세 시트 오픈', (tester) async {
    await pumpMyBooksSection(tester, (
      userBook: _readingUserBook,
      ctaType: 'reading',
    ));

    await tester.tap(find.text(_readingUserBook.book!.title));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  });

  testWidgets('CTA 버튼 탭 → 시트 없이 피드백 라우팅 (1탭 저니 회귀 방지)', (tester) async {
    final pushedRoutes = <String>[];
    await pumpMyBooksSection(
      tester,
      (userBook: _needsFeedbackUserBook, ctaType: 'needsFeedback'),
      pushedRoutes: pushedRoutes,
    );

    await tester.tap(find.text('피드백 남기기'));
    await tester.pumpAndSettle();

    expect(pushedRoutes.last, startsWith('/feedback/'));
    expect(find.byKey(const Key('sheet_expand_chevron')), findsNothing);
  });
}
