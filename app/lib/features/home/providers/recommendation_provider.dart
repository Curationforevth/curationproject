import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/services/impression_logger.dart';
import '../../../core/services/recommendation_service.dart';

final recommendationServiceProvider = Provider<RecommendationService>((ref) {
  return RecommendationService();
});

/// 당겨서 새로고침 여부. onRefresh 가 true 로 켜고 invalidate → homeFeedProvider 가
/// force-refresh 로 서버 시간캐시를 건너뛰어 새 큐레이션을 받는다. await 후 다시 false.
/// (watch 가 아니라 read 로 소비 — 값 변경만으로 provider 를 재빌드하지 않는다.)
final homeForceRefreshProvider = StateProvider<bool>((ref) => false);

/// 큐레이션 섹션(homeFeedProvider) 로드 실패 시 자동 재시도 횟수.
/// 3회(5s/15s/30s 백오프) 소진되면 수동 "다시 시도" 버튼으로 전환한다.
final homeFeedRetryProvider = StateProvider<int>((ref) => 0);

/// 추천(recommendationsProvider) computing=true 상태 자동 폴링 횟수.
/// 10회(6초 간격, 최대 60초) 소진되면 수동 "다시 시도" 버튼으로 전환한다.
final recomputePollProvider = StateProvider<int>((ref) => 0);

/// "관심 없어요" 를 누른 책 id 셋 — 추천/비슷한책/화제의책 카드를 그리는
/// 위젯들이 이 셋에 있는 book_id 를 즉시 필터링한다(optimistic hide).
/// 서버 서빙 필터(user_book_signals)가 정본이라 재조회 후에도 다시 안 뜨지만,
/// 재조회 전(다음 recompute/캐시 갱신 전)까지의 즉시 반영용 — 세션 로컬,
/// 영속화하지 않는다(v1 범위: 되돌리기 UI 없음).
final hiddenBookIdsProvider = StateProvider<Set<String>>((ref) => {});

/// 홈 피드 — 큐레이션/트렌딩/맞춤추천/비슷한책 섹션. 피드백 후 무효화되면 재요청.
final homeFeedProvider = FutureProvider<HomeFeed>((ref) async {
  final service = ref.watch(recommendationServiceProvider);
  final feed = await service.getHome(
    forceRefresh: ref.read(homeForceRefreshProvider),
  );
  // 노출 임프레션 로깅(섹션 책들).
  final bookIds = <String>[
    for (final s in feed.sections) ...s.books.map((b) => b.bookId),
  ];
  if (bookIds.isNotEmpty) {
    unawaited(
      ImpressionLogger(Supabase.instance.client).logImpressions(
        bookIds: bookIds,
        source: 'home_feed',
        algorithmVersion: 'h10_stage0',
      ),
    );
  }
  return feed;
});

final recommendationsProvider = FutureProvider<RecommendationResult>((
  ref,
) async {
  final service = ref.watch(recommendationServiceProvider);
  final result = await service.getRecommendations(limit: 10);
  unawaited(
    ImpressionLogger(Supabase.instance.client).logImpressions(
      bookIds: result.recommendations.map((b) => b.bookId).toList(),
      source: 'home_recommend',
      algorithmVersion: 'h10_stage0',
    ),
  );
  return result;
});

final similarBooksProvider =
    FutureProvider.family<List<RecommendedBook>, String>((ref, bookId) async {
      final service = ref.watch(recommendationServiceProvider);
      final books = await service.getSimilarBooks(bookId, limit: 10);
      unawaited(
        ImpressionLogger(Supabase.instance.client).logImpressions(
          bookIds: books.map((b) => b.bookId).toList(),
          source: 'similar',
          algorithmVersion: 'h10_stage0',
        ),
      );
      return books;
    });
