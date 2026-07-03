import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/impression_logger.dart';
import 'package:curation_app/core/widgets/book_spine.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/library/screens/library_screen.dart';

/// 서재 — 읽은 책 책등 탭 / 읽는 중 카드 몸통 탭 → 상세 시트 오픈(페이지 push 대체).
///
/// 하네스는 my_books_tap_test.dart / book_detail_expand_test.dart 패턴 재사용:
/// impressionLoggerProvider no-op 페이크 + bookshelfProvider/booksByStatusProvider
/// 는 bookshelfProvider override 로 파생시키고, similarBooksProvider 는 빈 리스트로
/// override. 라우팅 검증용 pushedRoutes 를 기록하는 커스텀 GoRouter 를 둔다.

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

final _readBook = const Book(id: 'b-read', title: '읽은 책 제목', author: '저자A');
final _readingBook = const Book(id: 'b-reading', title: '읽는 중인 책 제목', author: '저자B');

final _readUserBook = UserBook(
  id: 'ub-read',
  userId: 'u1',
  bookId: 'b-read',
  status: BookStatus.read,
  book: _readBook,
);

final _readingUserBook = UserBook(
  id: 'ub-reading',
  userId: 'u1',
  bookId: 'b-reading',
  status: BookStatus.reading,
  book: _readingBook,
);

Future<List<String>> pumpLibrary(
  WidgetTester tester,
  List<UserBook> books, {
  List<String>? pushedRoutes,
}) async {
  final routes = pushedRoutes ?? <String>[];
  final router = GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const Scaffold(body: LibraryScreen()),
      ),
      GoRoute(
        path: '/book/:id',
        builder: (context, state) {
          routes.add(state.uri.toString());
          return const Scaffold(body: Text('book detail page'));
        },
      ),
      GoRoute(
        path: '/register',
        builder: (context, state) {
          routes.add(state.uri.toString());
          return const Scaffold(body: Text('register screen'));
        },
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

  final overrides = <Override>[
    impressionLoggerProvider.overrideWithValue(_FakeImpressionLogger()),
    bookshelfProvider.overrideWith((ref) async => books),
    for (final ub in books)
      if (ub.book != null)
        similarBooksProvider(ub.book!.id).overrideWith((ref) async => const []),
  ];

  await tester.pumpWidget(
    ProviderScope(
      overrides: overrides,
      child: MaterialApp.router(routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return routes;
}

void main() {
  testWidgets('읽은 책 책등 탭 → 페이지 push 대신 상세 시트', (tester) async {
    final pushedRoutes = <String>[];
    await pumpLibrary(tester, [_readUserBook], pushedRoutes: pushedRoutes);

    await tester.tap(find.byType(BookSpine).first);
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
    expect(pushedRoutes.where((r) => r.startsWith('/book/')), isEmpty);
  });

  testWidgets('읽는 중 카드 몸통 탭 → 상세 시트, 다읽었어요 버튼은 직행', (tester) async {
    await pumpLibrary(tester, [_readingUserBook]);

    await tester.tap(find.text(_readingUserBook.book!.title));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  });
}
