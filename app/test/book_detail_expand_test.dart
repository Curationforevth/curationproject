import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/impression_logger.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

/// 책 상세 시트 2단계화(peek 0.65 ↔ 확장 1.0) — DraggableScrollableSheet 검증.
///
/// BookDetailBottomSheet.show() 경유로 시트 전체(내부 DraggableScrollableSheet
/// 포함)를 pump 한다 — impressionLoggerProvider 시임 덕분에 Supabase.instance
/// 전역 초기화 없이도 가능하다(선행 리팩터, task-1-brief 참고).

/// no-op 페이크 — home_resilience_test.dart / onboarding_screen_test.dart 관례:
/// 더미 SupabaseClient 로 생성해 실제 네트워크를 타지 않게 한다.
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

final bookWithLongDescription = Book(
  id: 'b1',
  title: '아주 긴 설명을 가진 책',
  author: '저자',
  description: '가' * 400, // 3줄 클램프를 반드시 넘기는 길이
);

final _fixedSimilarBooks = [
  const RecommendedBook(
      bookId: 's1', score: 0.9, title: '비슷한 책 1', author: '작가1'),
  const RecommendedBook(
      bookId: 's2', score: 0.8, title: '비슷한 책 2', author: '작가2'),
];

Future<void> pumpSheet(WidgetTester tester, Book book) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        impressionLoggerProvider.overrideWithValue(_FakeImpressionLogger()),
        bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
        similarBooksProvider(book.id).overrideWith((ref) async => _fixedSimilarBooks),
      ],
      child: MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => BookDetailBottomSheet.show(context, book),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('peek 상태 — 셰브론 노출 + 설명 3줄 클램프 + 행동 버튼 접힘 없이 가시',
      (tester) async {
    await pumpSheet(tester, bookWithLongDescription);

    expect(find.byKey(const Key('sheet_expand_chevron')), findsOneWidget);
    final text =
        tester.widget<Text>(find.byKey(const Key('sheet_description')));
    expect(text.maxLines, 3);
    // 홈→등록 2탭 저니 보존: 미보유 책 3버튼이 peek(0.65)에서 스크롤 없이 히트 가능
    expect(find.text('읽었어요').hitTestable(), findsOneWidget);
    expect(find.text('읽는 중').hitTestable(), findsOneWidget);
  });

  testWidgets('셰브론 탭 → 확장: 설명 전문 + 비슷한 책 그리드 + 닫기 헤더',
      (tester) async {
    await pumpSheet(tester, bookWithLongDescription);
    await tester.tap(find.byKey(const Key('sheet_expand_chevron')));
    await tester.pumpAndSettle();

    final text =
        tester.widget<Text>(find.byKey(const Key('sheet_description')));
    expect(text.maxLines, isNull);
    expect(find.byKey(const Key('sheet_similar_grid')), findsOneWidget);
    expect(find.byKey(const Key('sheet_close_button')), findsOneWidget);
  });

  testWidgets('확장 헤더 ✕ 탭 → 시트 닫힘', (tester) async {
    await pumpSheet(tester, bookWithLongDescription);
    await tester.tap(find.byKey(const Key('sheet_expand_chevron')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sheet_close_button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sheet_description')), findsNothing);
  });
}
