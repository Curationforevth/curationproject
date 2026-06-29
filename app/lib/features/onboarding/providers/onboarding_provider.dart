import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../services/onboarding_service.dart';

final onboardingServiceProvider =
    Provider<OnboardingService>((ref) => OnboardingService());

/// 온보딩 그리드 풀 (fallback_curation + books, 표지/제목 dedup).
final onboardingPoolProvider = FutureProvider<List<Book>>((ref) async {
  final service = ref.read(onboardingServiceProvider);
  return service.fetchCurationPool();
});

/// 이번 세션에서 온보딩을 마쳤거나 건너뛴 경우 true.
/// (영속 X — 0권으로 건너뛰면 다음 실행에 재유도. 설계 결정 6.)
final onboardingDismissedProvider = StateProvider<bool>((ref) => false);
