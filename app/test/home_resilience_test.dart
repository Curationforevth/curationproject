import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/home/screens/home_screen.dart';
import 'package:curation_app/features/onboarding/providers/onboarding_provider.dart';

/// 홈 진입 시 서버 지연/실패에도 화면이 죽지 않는지 검증하는 테스트.
///
/// homeFeedProvider/recommendationsProvider/bookshelfProvider 를 오버라이드해
/// 실제 Supabase/HTTP 호출 없이 로딩/에러/컴퓨팅 상태를 시뮬레이션한다.
void main() {
  Widget wrap(Widget child, List<Override> overrides) {
    return ProviderScope(
      overrides: [
        // 서재가 비어도 온보딩으로 튀지 않도록 기본은 건너뜀 처리.
        onboardingDismissedProvider.overrideWith((ref) => true),
        ...overrides,
      ],
      child: MaterialApp(home: child),
    );
  }

  group('변경 1 — 큐레이션 섹션 스켈레톤 + 자동 재시도', () {
    testWidgets('homeFeedProvider pending 이면 스켈레톤을 렌더한다',
        (tester) async {
      final completer = Completer<HomeFeed>();
      await tester.pumpWidget(wrap(
        const HomeScreen(),
        [
          bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          homeFeedProvider.overrideWith((ref) => completer.future),
          recommendationsProvider.overrideWith((ref) async =>
              const RecommendationResult(
                  recommendations: [], hasFeedback: false, totalLiked: 0)),
        ],
      ));
      // 서재는 즉시 data 로 세팅되지만 homeFeed 는 pending 상태로 남는다.
      await tester.pump();

      expect(find.byKey(const ValueKey('curation_skeleton')), findsOneWidget);

      // pending Completer 정리 — 타이머/퓨처가 걸린 채 테스트가 끝나지 않도록.
      completer.complete(const HomeFeed(sections: []));
      await tester.pumpWidget(Container());
    });

    testWidgets('homeFeedProvider error 면 스켈레톤을 보여주고 5초 후 재시도 카운트가 오른다',
        (tester) async {
      await tester.pumpWidget(wrap(
        const HomeScreen(),
        [
          bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          homeFeedProvider.overrideWith((ref) => Future<HomeFeed>.error('boom')),
          recommendationsProvider.overrideWith((ref) async =>
              const RecommendationResult(
                  recommendations: [], hasFeedback: false, totalLiked: 0)),
        ],
      ));
      await tester.pump();
      await tester.pump(); // 에러 프레임 반영

      expect(find.byKey(const ValueKey('curation_skeleton')), findsOneWidget);

      final container = ProviderScope.containerOf(
        tester.element(find.byType(HomeScreen)),
      );
      expect(container.read(homeFeedRetryProvider), 0);

      await tester.pump(const Duration(seconds: 5));
      expect(container.read(homeFeedRetryProvider), 1);

      // 정리: 위젯 dispose 로 남은 타이머 정리.
      await tester.pumpWidget(Container());
    });

    testWidgets('재시도 3회 소진 시 다시 시도 버튼 노출, 탭하면 카운트 리셋', (tester) async {
      await tester.pumpWidget(wrap(
        const HomeScreen(),
        [
          bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          homeFeedRetryProvider.overrideWith((ref) => 3),
          homeFeedProvider.overrideWith((ref) => Future<HomeFeed>.error('boom')),
          recommendationsProvider.overrideWith((ref) async =>
              const RecommendationResult(
                  recommendations: [], hasFeedback: false, totalLiked: 0)),
        ],
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text('추천 서가를 불러오지 못했어요'), findsOneWidget);
      expect(find.widgetWithText(TextButton, '다시 시도'), findsWidgets);

      final container = ProviderScope.containerOf(
        tester.element(find.byType(HomeScreen)),
      );
      expect(container.read(homeFeedRetryProvider), 3);

      await tester.tap(find.widgetWithText(TextButton, '다시 시도').last);
      await tester.pump();

      expect(container.read(homeFeedRetryProvider), 0);

      await tester.pumpWidget(Container());
    });
  });

  group('변경 2 — 서재 로드 실패 시 홈 하드블로킹 제거', () {
    testWidgets('bookshelfProvider error 여도 배너 + _HomeContent 헤더가 렌더된다',
        (tester) async {
      await tester.pumpWidget(wrap(
        const HomeScreen(),
        [
          bookshelfProvider.overrideWith((ref) => Future<List<UserBook>>.error('네트워크 오류')),
          homeFeedProvider.overrideWith((ref) async => const HomeFeed(sections: [])),
          recommendationsProvider.overrideWith((ref) async =>
              const RecommendationResult(
                  recommendations: [], hasFeedback: false, totalLiked: 0)),
        ],
      ));
      await tester.pump();
      await tester.pump();

      expect(find.text('서재를 불러오지 못했어요'), findsOneWidget);
      // _HomeContent 헤더는 서재 상태와 무관하게 렌더돼야 한다.
      expect(find.textContaining('오늘은 어떤 책을'), findsOneWidget);

      await tester.pumpWidget(Container());
    });
  });

  group('변경 3 — 추천 computing 스켈레톤 자동 폴링', () {
    testWidgets('computing=true 결과 반환 시 6초 후 recomputePollProvider 가 1 증가한다',
        (tester) async {
      await tester.pumpWidget(wrap(
        const HomeScreen(),
        [
          bookshelfProvider.overrideWith((ref) async => <UserBook>[]),
          homeFeedProvider.overrideWith((ref) async => const HomeFeed(sections: [])),
          recommendationsProvider.overrideWith((ref) async =>
              const RecommendationResult(
                recommendations: [],
                hasFeedback: false,
                totalLiked: 3,
                computing: true,
              )),
        ],
      ));
      await tester.pump();
      await tester.pump();

      final container = ProviderScope.containerOf(
        tester.element(find.byType(HomeScreen)),
      );
      expect(container.read(recomputePollProvider), 0);

      await tester.pump(const Duration(seconds: 6));
      expect(container.read(recomputePollProvider), 1);

      await tester.pumpWidget(Container());
    });
  });
}
