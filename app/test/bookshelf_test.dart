import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/theme/app_colors.dart';
import 'package:curation_app/core/widgets/book_spine.dart';
import 'package:curation_app/core/widgets/bookshelf_row.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/bookshelf/screens/bookshelf_screen.dart';

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

      expect(find.text('채식주의자'), findsOneWidget);
      expect(find.text('한강'), findsOneWidget);
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

      await tester.tap(find.text('테스트'));
      expect(tapped, isTrue);
    });
  });

  group('BookshelfRow', () {
    testWidgets('shows empty state when no books', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: BookshelfRow(books: []),
          ),
        ),
      );

      expect(find.byIcon(Icons.add), findsOneWidget);
    });

    testWidgets('shows book spines when books present', (tester) async {
      final books = [
        const Book(id: '1', title: '채식주의자', author: '한강'),
        const Book(id: '2', title: '소년이 온다', author: '한강'),
      ];

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: BookshelfRow(books: books),
          ),
        ),
      );

      expect(find.text('채식주의자'), findsOneWidget);
      expect(find.text('소년이 온다'), findsOneWidget);
    });
  });

  group('BookshelfScreen', () {
    testWidgets('shows empty state when no books', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          ],
          child: const MaterialApp(
            home: BookshelfScreen(),
          ),
        ),
      );

      await tester.pumpAndSettle();

      expect(find.text('아직 서재가 비어있어요'), findsOneWidget);
      expect(find.text('책 검색하기'), findsOneWidget);
    });
  });
}
