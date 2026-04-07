import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/services/impression_logger.dart';
import '../../../core/services/recommendation_service.dart';

final recommendationServiceProvider = Provider<RecommendationService>((ref) {
  return RecommendationService();
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
