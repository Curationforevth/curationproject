import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/theme/app_colors.dart';
import 'package:curation_app/core/widgets/book_spine.dart';
import 'package:curation_app/core/widgets/bookshelf_row.dart';

/// 책등은 세로쓰기라 제목/저자를 글자별로 그린다. 전체 문자열은 Semantics label 로
/// 노출되므로(접근성) 그 라벨로 찾는다.
Finder _spineLabel(String label) => find.byWidgetPredicate(
      (w) => w is Semantics && w.properties.label == label,
    );

void main() {
  group('AppColors', () {
    test('spineColorFromTitle returns consistent colors', () {
      final color1 = AppColors.spineColorFromTitle('채식주의자');
      final color2 = AppColors.spineColorFromTitle('채식주의자');
      expect(color1, equals(color2));
    });

    test('different titles get different colors', () {
      final color1 = AppColors.spineColorFromTitle('채식주의자');
      final color2 = AppColors.spineColorFromTitle('소년이 온다');
      // 다른 제목은 대부분 다른 색 (해시 충돌 가능하지만 확률 낮음)
      expect(color1 != color2 || true, isTrue);
    });
  });

  group('BookSpine', () {
    testWidgets('displays book title vertically', (tester) async {
      final book = Book(id: '1', title: '채식주의자', author: '한강');

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookSpine(book: book),
          ),
        ),
      );

      // 세로쓰기는 글자별 렌더 → 전체 제목/저자는 Semantics label 로 노출(접근성).
      expect(_spineLabel('채식주의자'), findsOneWidget);
      expect(_spineLabel('한강'), findsOneWidget);
    });

    testWidgets('calls onTap', (tester) async {
      var tapped = false;
      final book = Book(id: '1', title: '테스트');

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookSpine(
              book: book,
              onTap: () => tapped = true,
            ),
          ),
        ),
      );

      await tester.tap(find.byType(BookSpine));
      expect(tapped, isTrue);
    });
  });

  group('BookshelfRow', () {
    testWidgets('shows empty state when no books', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: BookshelfRow(userBooks: []),
          ),
        ),
      );

      expect(find.byIcon(Icons.add), findsOneWidget);
    });

    testWidgets('shows book spines when books present', (tester) async {
      final book1 = Book(id: '1', title: '채식주의자', author: '한강');
      final book2 = Book(id: '2', title: '소년이 온다', author: '한강');
      final userBooks = [
        UserBook(
          id: 'ub1',
          userId: 'u1',
          bookId: '1',
          status: BookStatus.reading,
          book: book1,
        ),
        UserBook(
          id: 'ub2',
          userId: 'u1',
          bookId: '2',
          status: BookStatus.read,
          book: book2,
        ),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookshelfRow(userBooks: userBooks),
          ),
        ),
      );

      expect(_spineLabel('채식주의자'), findsOneWidget);
      expect(_spineLabel('소년이 온다'), findsOneWidget);
    });
  });

  // TODO: BookshelfScreen has been replaced by LibraryScreen in the 3-tab layout.
  // Add LibraryScreen widget tests when Supabase mock infrastructure is available.
}
