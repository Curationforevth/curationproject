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

/// 평가를 남긴 읽은 책 = 시트가 곧 평가 조회 화면 (/book 페이지 조회 역할 흡수).
/// 회귀 방지: "평가한 내용을 볼 곳이 없다"(Eden 리포트)가 다시 생기면 여기가 깨진다.

class _FakeImpressionLogger extends ImpressionLogger {
  _FakeImpressionLogger()
      : super(SupabaseClient(
          'http://localhost',
          'anon-key',
          authOptions: const AuthClientOptions(autoRefreshToken: false),
        ));

  @override
  Future<void> logAction(
      {required String bookId, required String action}) async {}

  @override
  Future<void> logImpressions({
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) async {}
}

final _book = Book(id: 'b1', title: '읽고 평가한 책', author: '저자');

final _ratedUserBook = UserBook(
  id: 'ub1',
  userId: 'u1',
  bookId: 'b1',
  status: BookStatus.read,
  rating: 'good',
  emotionTags: const ['문체', '분위기'],
  reviewText: '문장이 오래 남는다',
  book: _book,
);

Future<void> _pumpSheet(WidgetTester tester, UserBook userBook) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        impressionLoggerProvider.overrideWithValue(_FakeImpressionLogger()),
        bookshelfProvider.overrideWith((ref) async => [userBook]),
        similarBooksProvider(_book.id)
            .overrideWith((ref) async => <RecommendedBook>[]),
      ],
      child: MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => ElevatedButton(
              onPressed: () => BookDetailBottomSheet.show(context, _book),
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
  testWidgets('평가한 읽은 책 — 시트에 내 평가(호오·태그·감상)와 수정 진입 노출', (tester) async {
    await _pumpSheet(tester, _ratedUserBook);

    expect(find.text('내 평가'), findsOneWidget);
    expect(find.text('👍 좋았어요'), findsOneWidget);
    expect(find.text('#문체'), findsOneWidget);
    expect(find.text('#분위기'), findsOneWidget);
    expect(find.text('문장이 오래 남는다'), findsOneWidget);
    expect(find.text('수정'), findsOneWidget);
    // 섹션이 기존 큰 버튼을 대체 — 중복 진입점 없음
    expect(find.text('내 평가 보기 · 수정'), findsNothing);
  });

  testWidgets('평가 없는 읽은 책 — 내 평가 섹션 대신 [평가 남기기] 버튼', (tester) async {
    final unrated = UserBook(
      id: 'ub2',
      userId: 'u1',
      bookId: 'b1',
      status: BookStatus.read,
      book: _book,
    );
    await _pumpSheet(tester, unrated);

    expect(find.text('내 평가'), findsNothing);
    expect(find.text('평가 남기기'), findsOneWidget);
  });
}
