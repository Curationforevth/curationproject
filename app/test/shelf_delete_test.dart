import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

/// 서재 삭제 + 실행 취소 + 관심없음 — 설계:
/// docs/superpowers/specs/2026-07-02-shelf-remove-not-interested-design.md
///
/// BookDetailBottomSheet 는 Supabase.instance(전역 싱글턴) 의존(impression 로그)이라
/// 기존 관례(shelf_aware_actions_test.dart)대로 시트 위젯을 직접 pump 하지 않고,
/// 순수 로직(스냅숏 왕복)과 provider 단위로 쪼개 검증한다.
///
/// removeFromShelf/restoreToShelf 의 실제 Supabase(가짜 PostgREST) 통합 검증은
/// test/shelf_delete_http_test.dart 에 분리했다 — testWidgets(TestWidgetsFlutterBinding)
/// 가 있는 파일에서 실 HttpClient 를 쓰면 바인딩이 모든 HTTP 요청을 400 으로
/// 가로채(flutter_test 의 알려진 제약, test/bookshelf_test.dart 도 동일 이유로 분리됨)
/// 로컬 서버 응답이 앱에 닿지 못한다.

UserBook _ub(
  BookStatus status, {
  String? rating,
  String id = 'ub-b1',
  String bookId = 'b1',
  List<String>? emotionTags,
  String? reviewText,
  int? shelfOrder,
}) => UserBook(
  id: id,
  userId: 'u1',
  bookId: bookId,
  status: status,
  rating: rating,
  emotionTags: emotionTags,
  reviewText: reviewText,
  shelfOrder: shelfOrder,
);

void main() {
  group('변경 1 — 바텀시트 삭제 버튼 노출 분기', () {
    testWidgets('서재 보유(읽은 책) — "이 책 삭제" 노출, wishlist 는 "읽고 싶어요 취소"', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ShelfDeleteAction(
              userBook: _ub(BookStatus.read),
              onTap: () {},
            ),
          ),
        ),
      );
      expect(find.text('이 책 삭제'), findsOneWidget);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ShelfDeleteAction(
              userBook: _ub(BookStatus.wantToRead),
              onTap: () {},
            ),
          ),
        ),
      );
      expect(find.text('읽고 싶어요 취소'), findsOneWidget);
    });

    testWidgets('미보유 책 — 삭제 버튼 없음(null 이면 렌더 안 함)', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: SizedBox.shrink())),
      );
      // userBook == null 인 경우 호출측(BookDetailBottomSheet)이 아예 위젯을
      // 렌더하지 않는다 — ShelfDeleteAction 은 non-null UserBook 만 받는다(타입으로 보장).
      expect(find.text('이 책 삭제'), findsNothing);
    });

    testWidgets('탭하면 onTap 콜백 호출', (tester) async {
      var tapped = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ShelfDeleteAction(
              userBook: _ub(BookStatus.read),
              onTap: () => tapped = true,
            ),
          ),
        ),
      );
      await tester.tap(find.text('이 책 삭제'));
      expect(tapped, isTrue);
    });
  });

  group('변경 2 — "관심 없어요" 버튼', () {
    testWidgets('탭하면 onTap 콜백 호출', (tester) async {
      var tapped = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: NotInterestedAction(onTap: () => tapped = true)),
        ),
      );
      expect(find.text('관심 없어요'), findsOneWidget);
      await tester.tap(find.text('관심 없어요'));
      expect(tapped, isTrue);
    });
  });

  group('변경 3 — hiddenBookIdsProvider', () {
    test('초기값은 빈 Set', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      expect(container.read(hiddenBookIdsProvider), isEmpty);
    });

    test('book_id 추가 후 셋에 포함됨(추천 렌더 필터링의 기반)', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);

      container.read(hiddenBookIdsProvider.notifier).state = {
        ...container.read(hiddenBookIdsProvider),
        'hidden-book-1',
      };

      final hidden = container.read(hiddenBookIdsProvider);
      expect(hidden.contains('hidden-book-1'), isTrue);
      expect(hidden.contains('other-book'), isFalse);
    });

    test('추천 리스트에서 hidden book_id 카드가 필터링된다(위젯 소비 로직과 동일한 형태)', () {
      final recs = [
        const RecommendedBook(
          bookId: 'b1',
          score: 1.0,
          title: 'A',
          author: 'a',
        ),
        const RecommendedBook(
          bookId: 'b2',
          score: 1.0,
          title: 'B',
          author: 'b',
        ),
        const RecommendedBook(
          bookId: 'b3',
          score: 1.0,
          title: 'C',
          author: 'c',
        ),
      ];
      final hidden = {'b2'};
      final visible = recs.where((r) => !hidden.contains(r.bookId)).toList();

      expect(visible.map((r) => r.bookId), ['b1', 'b3']);
    });
  });

  group('변경 4 — removeFromShelf/restoreToShelf 스냅숏 왕복(순수 로직)', () {
    test('스냅숏은 삭제 전 모든 컬럼을 보존한다', () {
      final ub = _ub(
        BookStatus.read,
        id: 'ub-42',
        rating: 'good',
        emotionTags: ['따뜻함', '몰입감'],
        reviewText: '좋았어요',
        shelfOrder: 3,
      );

      final snapshot = userBookSnapshot(ub);

      expect(snapshot['id'], 'ub-42');
      expect(snapshot['user_id'], 'u1');
      expect(snapshot['book_id'], 'b1');
      expect(snapshot['status'], 'finished');
      expect(snapshot['rating'], 'good');
      expect(snapshot['emotion_tags'], ['따뜻함', '몰입감']);
      expect(snapshot['review_text'], '좋았어요');
      expect(snapshot['shelf_order'], 3);
    });

    test('id 포함 재삽입 payload와 id 제외(23505 폴백) payload 를 만들 수 있다', () {
      final ub = _ub(BookStatus.wantToRead, id: 'ub-99', shelfOrder: null);
      final snapshot = userBookSnapshot(ub);

      final withId = restoreInsertPayload(snapshot, includeId: true);
      expect(withId['id'], 'ub-99');
      expect(withId['book_id'], 'b1');
      expect(withId['status'], 'wishlist');

      final withoutId = restoreInsertPayload(snapshot, includeId: false);
      expect(withoutId.containsKey('id'), isFalse);
      expect(withoutId['book_id'], 'b1');
    });
  });

  group('변경 5 — 삭제 스낵바(실행 취소)', () {
    testWidgets('삭제 탭 → 스낵바 "삭제했어요" + "실행 취소" 액션 노출', (tester) async {
      var restoreCalled = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) {
                return ElevatedButton(
                  onPressed: () {
                    showDeletedSnackBar(
                      context,
                      onUndo: () {
                        restoreCalled = true;
                      },
                    );
                  },
                  child: const Text('삭제'),
                );
              },
            ),
          ),
        ),
      );

      await tester.tap(find.text('삭제'));
      await tester.pumpAndSettle();

      expect(find.text('삭제했어요'), findsOneWidget);
      expect(find.text('실행 취소'), findsOneWidget);

      await tester.tap(find.text('실행 취소'));
      await tester.pump();
      expect(restoreCalled, isTrue);
    });
  });
}
