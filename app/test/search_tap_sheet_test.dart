import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/book_registration_service.dart';
import 'package:curation_app/core/services/book_search_service.dart'
    show BookSearchService, BookSearchResult;
import 'package:curation_app/core/services/impression_logger.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/search/providers/book_search_provider.dart';
import 'package:curation_app/features/search/screens/book_search_screen.dart';
import 'package:curation_app/features/search/utils/resolve_sheet_book.dart';

/// Task 4 — 검색/등록 탭 = 상세 시트 통일 (ISBN 서재 매칭).
///
/// 옛 "읽기 상태 선택" bottom sheet 를 폐기하고 BookDetailBottomSheet 로
/// 통일한다. 검색 결과 Book(id='')이 이미 서재에 등록된 책(ISBN 일치)이면
/// resolveSheetBook 이 실 id 를 가진 서재 Book 으로 치환해 죽은 탭을 막는다.

/// no-op 페이크 — book_detail_expand_test.dart 관례.
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

class FakeBookSearchService implements BookSearchService {
  final List<Book> booksToReturn;
  FakeBookSearchService({this.booksToReturn = const []});

  @override
  Future<BookSearchResult> search(String query, {int page = 1, int size = 20}) async {
    return BookSearchResult(books: booksToReturn, isEnd: true);
  }

  @override
  Future<void> cacheBook(Book book) async {}
}

class FakeBookRegistrationService implements BookRegistrationService {
  @override
  Future<String> registerBook(Book book, dynamic status) async => 'fake-id';

  @override
  Future<bool> isBookInShelf(String? isbn) async => false;

  @override
  Future<Set<String>> getShelfIsbns() async => {};
}

Book searchBook({required String isbn}) {
  return Book(id: '', isbn: isbn, title: '테스트 책', author: '테스트 저자');
}

void main() {
  group('resolveSheetBook', () {
    testWidgets('서재에 같은 isbn 있으면 실 id Book 반환', (tester) async {
      final shelvedBook = Book(
        id: 'real-id',
        isbn: '9791100000001',
        title: '테스트 책',
        author: '테스트 저자',
      );
      final userBook = UserBook(
        id: 'ub-1',
        userId: 'user-1',
        bookId: 'real-id',
        status: BookStatus.reading,
        book: shelvedBook,
      );

      late WidgetRef capturedRef;
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            bookshelfProvider.overrideWith((ref) async => [userBook]),
          ],
          child: MaterialApp(
            home: Consumer(
              builder: (context, ref, _) {
                // watch 로 bookshelfProvider 를 구독해야 FutureProvider 가
                // resolve 되고 pumpAndSettle 이 그 완료를 기다린다.
                ref.watch(bookshelfProvider);
                capturedRef = ref;
                return const SizedBox();
              },
            ),
          ),
        ),
      );
      // bookshelfProvider가 값으로 resolve될 때까지 대기.
      await tester.pumpAndSettle();

      final resolved = resolveSheetBook(
        capturedRef,
        searchBook(isbn: '9791100000001'),
      );
      expect(resolved.id, 'real-id');
    });

    testWidgets('서재에 없으면 검색 Book 그대로(id 빈값)', (tester) async {
      late WidgetRef capturedRef;
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          ],
          child: MaterialApp(
            home: Consumer(
              builder: (context, ref, _) {
                ref.watch(bookshelfProvider);
                capturedRef = ref;
                return const SizedBox();
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final resolved = resolveSheetBook(
        capturedRef,
        searchBook(isbn: '9791100000002'),
      );
      expect(resolved.id, '');
    });
  });

  group('BookSearchScreen — 탭 통일', () {
    testWidgets('기등록 책 탭도 살아있고 상세 시트가 뜬다 (죽은 탭 소멸)', (tester) async {
      final shelvedBook = Book(
        id: 'real-id',
        isbn: '9791100000001',
        title: '테스트 책',
        author: '테스트 저자',
      );
      final userBook = UserBook(
        id: 'ub-1',
        userId: 'user-1',
        bookId: 'real-id',
        status: BookStatus.reading,
        book: shelvedBook,
      );
      final resultBook = searchBook(isbn: '9791100000001');

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            bookSearchServiceProvider.overrideWithValue(
              FakeBookSearchService(booksToReturn: [resultBook]),
            ),
            registrationServiceProvider
                .overrideWithValue(FakeBookRegistrationService()),
            bookshelfProvider.overrideWith((ref) async => [userBook]),
            impressionLoggerProvider.overrideWithValue(_FakeImpressionLogger()),
            similarBooksProvider('real-id').overrideWith((ref) async => []),
          ],
          child: const MaterialApp(
            home: BookSearchScreen(),
          ),
        ),
      );

      // 검색어 입력 → 디바운스(500ms) 대기 → 결과 로드.
      await tester.enterText(find.byType(TextField), '테스트');
      await tester.pump(const Duration(milliseconds: 600));
      await tester.pumpAndSettle();

      expect(find.text('테스트 책'), findsOneWidget);

      await tester.tap(find.text('테스트 책'));
      await tester.pumpAndSettle();

      // 상세 시트가 떴는지 확인.
      expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
      // 옛 시트 텍스트는 어디에도 없음.
      expect(find.text('읽기 상태 선택'), findsNothing);
    });
  });
}
