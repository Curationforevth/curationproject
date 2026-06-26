import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/services/impression_logger.dart';
import '../../../core/services/recommendation_service.dart';

final recommendationServiceProvider = Provider<RecommendationService>((ref) {
  return RecommendationService();
});

/// 홈 피드 — 큐레이션/트렌딩/맞춤추천/비슷한책 섹션. 피드백 후 무효화되면 재요청.
final homeFeedProvider = FutureProvider<HomeFeed>((ref) async {
  final service = ref.watch(recommendationServiceProvider);
  final feed = await service.getHome();
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

final recommendationsProvider =
    FutureProvider<RecommendationResult>((ref) async {
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
    FutureProvider.family<List<RecommendedBook>, String>(
        (ref, bookId) async {
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
