import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/services/book_search_service.dart';
import 'package:curation_app/core/services/book_registration_service.dart';
import 'package:curation_app/features/search/providers/book_search_provider.dart';
import 'package:curation_app/features/search/screens/book_search_screen.dart';
import 'package:curation_app/features/search/widgets/book_search_result_card.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';

class FakeBookSearchService implements BookSearchService {
  @override
  Future<List<Book>> search(String query, {int page = 1, int size = 20}) async {
    return [];
  }

  @override
  Future<void> cacheBook(Book book) async {}
}

class FakeBookRegistrationService implements BookRegistrationService {
  @override
  Future<void> registerBook(Book book, dynamic status) async {}

  @override
  Future<bool> isBookInShelf(String? isbn) async => false;

  @override
  Future<Set<String>> getShelfIsbns() async => {};
}

void main() {
  group('BookSearchService JSON parsing', () {
    test('카카오 API 응답 형식으로 Book 생성', () {
      // 카카오 API 응답을 Book.fromJson이 아닌 서비스에서 변환하므로
      // 여기서는 Book 모델의 기본 생성/직렬화를 테스트
      final book = Book(
        id: '',
        isbn: '9788936434267',
        title: '채식주의자',
        author: '한강',
        publisher: '창비',
        coverUrl: 'https://example.com/cover.jpg',
        description: '소설 내용...',
        source: 'kakao',
      );

      expect(book.title, '채식주의자');
      expect(book.source, 'kakao');

      final json = book.toJson();
      expect(json['source'], 'kakao');
      expect(json['cover_url'], 'https://example.com/cover.jpg');
    });
  });

  group('BookSearchResultCard', () {
    testWidgets('displays book info', (tester) async {
      final book = Book(
        id: '1',
        title: '채식주의자',
        author: '한강',
        description: '소설 내용...',
        coverUrl: null,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookSearchResultCard(book: book),
          ),
        ),
      );

      expect(find.text('채식주의자'), findsOneWidget);
      expect(find.text('한강'), findsOneWidget);
      expect(find.text('소설 내용...'), findsOneWidget);
    });

    testWidgets('calls onTap', (tester) async {
      var tapped = false;
      final book = Book(id: '1', title: '테스트');

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookSearchResultCard(
              book: book,
              onTap: () => tapped = true,
            ),
          ),
        ),
      );

      await tester.tap(find.text('테스트'));
      expect(tapped, isTrue);
    });
  });

  group('BookSearchScreen', () {
    testWidgets('shows search hint initially', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            bookSearchServiceProvider
                .overrideWithValue(FakeBookSearchService()),
            registrationServiceProvider
                .overrideWithValue(FakeBookRegistrationService()),
          ],
          child: const MaterialApp(
            home: BookSearchScreen(),
          ),
        ),
      );

      expect(find.text('책을 검색해보세요'), findsOneWidget);
      expect(find.byType(TextField), findsOneWidget);
    });
  });

  group('BookSearchState', () {
    test('initial state is idle with empty results', () {
      const state = BookSearchState();
      expect(state.status, BookSearchStatus.idle);
      expect(state.results, isEmpty);
      expect(state.errorMessage, isNull);
    });

    test('copyWith updates fields', () {
      const state = BookSearchState();
      final updated = state.copyWith(
        status: BookSearchStatus.loading,
      );
      expect(updated.status, BookSearchStatus.loading);
      expect(updated.results, isEmpty);
    });
  });
}
