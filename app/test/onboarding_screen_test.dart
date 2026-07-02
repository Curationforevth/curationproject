import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';
import 'package:curation_app/features/onboarding/providers/onboarding_provider.dart';
import 'package:curation_app/features/onboarding/screens/onboarding_screen.dart';
import 'package:curation_app/features/onboarding/services/onboarding_service.dart';

/// Supabase 호출 없이 completeOnboarding 을 가짜로 처리하는 테스트용 서비스.
/// fetchCurationPool 은 고정 5권 풀을 반환한다(그리드 진행 가능 최소치).
/// 더미 SupabaseClient 를 넘겨 OnboardingService 기본 생성자가
/// Supabase.instance(전역 초기화 필요)에 접근하지 않게 한다.
/// autoRefreshToken: false — GoTrueClient 의 주기 타이머가 테스트 종료 후
/// "pending timer" 로 남아 실패하는 것을 막는다.
class _FakeOnboardingService extends OnboardingService {
  final bool shouldFail;
  bool completeCalled = false;

  _FakeOnboardingService({this.shouldFail = false})
      : super(SupabaseClient(
          'http://localhost',
          'anon-key',
          authOptions: const AuthClientOptions(autoRefreshToken: false),
        ));

  @override
  Future<List<Book>> fetchCurationPool({int limit = 60}) async {
    return List.generate(
      5,
      (i) => Book(id: 'b$i', title: '책$i', coverUrl: 'http://x/$i.jpg'),
    );
  }

  @override
  Future<void> completeOnboarding({
    required List<String> selectedBookIds,
    String? favoriteBookId,
    List<String> favoriteEmotionTags = const [],
  }) async {
    completeCalled = true;
    if (shouldFail) throw Exception('저장 실패');
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

void main() {
  Widget wrap(
    _FakeOnboardingService service, {
    _FakeRecommendationService? recService,
  }) {
    return ProviderScope(
      overrides: [
        onboardingServiceProvider.overrideWithValue(service),
        // 온보딩 자체가 바꾸는 상태만 검증 — 서재/추천 재조회는 실제 네트워크로
        // 새지 않도록 고정값으로 오버라이드. recommendationServiceProvider 도 대체해
        // triggerRecompute() 가 미초기화 Supabase.instance 를 건드리지 않게 한다.
        recommendationServiceProvider
            .overrideWithValue(recService ?? _FakeRecommendationService()),
        bookshelfProvider.overrideWith((ref) async => []),
        recommendationsProvider.overrideWith((ref) async =>
            const RecommendationResult(
                recommendations: [], hasFeedback: false, totalLiked: 0)),
      ],
      child: const MaterialApp(
        home: Scaffold(body: OnboardingScreen()),
      ),
    );
  }

  Future<void> goToGrid(WidgetTester tester) async {
    await tester.tap(find.text('시작하기'));
    await tester.pumpAndSettle();
  }

  /// 그리드 타일을 책 제목으로 찾아 탭한다(스크롤 밖일 수 있어 ensureVisible 선행).
  Future<void> tapBook(WidgetTester tester, int i) async {
    final finder = find.text('책$i');
    await tester.ensureVisible(finder);
    await tester.pumpAndSettle();
    await tester.tap(finder);
  }

  group('변경 3 — 그리드 5권 최소 강제', () {
    testWidgets('4권 선택 시 다음 버튼 비활성 + 안내 문구', (tester) async {
      final service = _FakeOnboardingService();
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();
      await goToGrid(tester);

      // 4권만 탭.
      for (var i = 0; i < 4; i++) {
        await tapBook(tester, i);
      }
      await tester.pump();

      expect(find.text('1권만 더 골라주세요'), findsOneWidget);

      // 버튼 비활성 — 탭해도 다음 단계로 넘어가지 않는다.
      await tester.tap(find.text('1권만 더 골라주세요'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('grid')), findsOneWidget);
      expect(find.byKey(const ValueKey('favorite')), findsNothing);
    });

    testWidgets('5권 선택 시 다음 버튼 활성 + 다음 단계 진입', (tester) async {
      final service = _FakeOnboardingService();
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();
      await goToGrid(tester);

      for (var i = 0; i < 5; i++) {
        await tapBook(tester, i);
      }
      await tester.pump();

      expect(find.text('다음'), findsOneWidget);

      await tester.tap(find.text('다음'));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('favorite')), findsOneWidget);
    });

    testWidgets('0권일 때 안내 문구', (tester) async {
      final service = _FakeOnboardingService();
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();
      await goToGrid(tester);

      expect(find.text('읽은 책을 5권 이상 골라주세요'), findsOneWidget);
    });
  });

  group('변경 4 — 완료 연출', () {
    testWidgets('저장 성공 후 "당신의 서재가 시작됐어요" 노출, 시작하기 탭하면 dismiss', (tester) async {
      final service = _FakeOnboardingService();
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();
      await goToGrid(tester);

      for (var i = 0; i < 5; i++) {
        await tapBook(tester, i);
      }
      await tester.pump();
      await tester.tap(find.text('다음'));
      await tester.pumpAndSettle();

      // Step 2(최애) — 완료 탭.
      await tester.tap(find.text('완료'));
      await tester.pumpAndSettle();

      expect(service.completeCalled, isTrue);
      expect(find.text('당신의 서재가 시작됐어요'), findsOneWidget);
      expect(find.text('시작하기'), findsOneWidget);

      final element = tester.element(find.byType(OnboardingScreen));
      final container = ProviderScope.containerOf(element);
      expect(container.read(onboardingDismissedProvider), isFalse);

      await tester.tap(find.text('시작하기'));
      await tester.pump();

      expect(container.read(onboardingDismissedProvider), isTrue);
    });

    testWidgets('저장 실패 시 완료 화면으로 넘어가지 않고 스낵바 노출', (tester) async {
      final service = _FakeOnboardingService(shouldFail: true);
      await tester.pumpWidget(wrap(service));
      await tester.pumpAndSettle();
      await goToGrid(tester);

      for (var i = 0; i < 5; i++) {
        await tapBook(tester, i);
      }
      await tester.pump();
      await tester.tap(find.text('다음'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('완료'));
      await tester.pumpAndSettle();

      expect(find.text('당신의 서재가 시작됐어요'), findsNothing);
      expect(find.textContaining('서재 저장 중 오류가 났어요'), findsOneWidget);
    });
  });
}
