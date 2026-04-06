import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/services/recommendation_service.dart';

final recommendationServiceProvider = Provider<RecommendationService>((ref) {
  return RecommendationService();
});

final recommendationsProvider =
    FutureProvider<RecommendationResult>((ref) async {
  final service = ref.watch(recommendationServiceProvider);
  return service.getRecommendations(limit: 10);
});

final similarBooksProvider =
    FutureProvider.family<List<RecommendedBook>, String>(
        (ref, bookId) async {
  final service = ref.watch(recommendationServiceProvider);
  return service.getSimilarBooks(bookId, limit: 10);
});
