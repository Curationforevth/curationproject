import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/book_registration_service.dart';
import 'package:curation_app/core/services/impression_logger.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
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

/// registerBook 호출을 기록하는 가짜 등록 서비스 — "다 읽었어요" 쓰기가
/// raw Supabase update 가 아니라 정본 경로(BookRegistrationService→
/// resolveShelfWrite)를 타는지 검증용(전수 QA 결함 회귀 방지).
class _FakeRegistrationService extends BookRegistrationService {
  _FakeRegistrationService()
      : super(SupabaseClient(
          'http://localhost',
          'anon-key',
          authOptions: const AuthClientOptions(autoRefreshToken: false),
        ));

  final registerCalls = <({Book book, BookStatus status})>[];

  @override
  Future<String> registerBook(Book book, BookStatus status) async {
    registerCalls.add((book: book, status: status));
    return 'ub-registered';
  }
}

/// triggerRecompute 호출 여부만 기록하는 가짜 추천 서비스.
/// (RecommendationService 는 Supabase.instance 를 내부에서 직접 참조해
/// 전역 초기화 없이는 호출할 수 없다 — provider 레벨에서 대체한다.)
class _FakeRecommendationService extends RecommendationService {
  bool triggerCalled = false;

  @override
  Future<void> triggerRecompute() async {
    triggerCalled = true;
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

Future<List<String>> _pumpLibrary(
  WidgetTester tester,
  List<UserBook> books, {
  List<String>? pushedRoutes,
  _FakeRegistrationService? regService,
  _FakeRecommendationService? recService,
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
    registrationServiceProvider
        .overrideWithValue(regService ?? _FakeRegistrationService()),
    recommendationServiceProvider
        .overrideWithValue(recService ?? _FakeRecommendationService()),
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
    await _pumpLibrary(tester, [_readUserBook], pushedRoutes: pushedRoutes);

    await tester.tap(find.byType(BookSpine).first);
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
    expect(pushedRoutes.where((r) => r.startsWith('/book/')), isEmpty);
  });

  testWidgets('읽는 중 카드 몸통 탭 → 상세 시트, 다읽었어요 버튼은 직행', (tester) async {
    await _pumpLibrary(tester, [_readingUserBook]);

    await tester.tap(find.text(_readingUserBook.book!.title));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
  });

  testWidgets('다 읽었어요 버튼 → 정본 경로(registerBook read 전이+recompute) 후 피드백 라우팅',
      (tester) async {
    final pushedRoutes = <String>[];
    final regService = _FakeRegistrationService();
    final recService = _FakeRecommendationService();
    await _pumpLibrary(
      tester,
      [_readingUserBook],
      pushedRoutes: pushedRoutes,
      regService: regService,
      recService: recService,
    );

    await tester.tap(find.text('다 읽었어요'));
    await tester.pumpAndSettle();

    // raw Supabase update 회귀 방지 — 쓰기는 반드시 registerBook
    // (resolveShelfWrite 전략: 전이/23505 안전망)을 경유해야 한다.
    expect(regService.registerCalls, hasLength(1));
    expect(regService.registerCalls.single.book.id, _readingBook.id);
    expect(regService.registerCalls.single.status, BookStatus.read);
    // 완독 신호가 서버 추천 재계산에 반영되도록 recompute 트리거 필수.
    expect(recService.triggerCalled, isTrue);
    // 등록이 돌려준 userBookId 로 피드백 라우팅.
    expect(pushedRoutes.last, '/feedback/ub-registered');
  });
}
