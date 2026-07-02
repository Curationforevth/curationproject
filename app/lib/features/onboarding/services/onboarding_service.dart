import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/book.dart';

/// 온보딩 그리드 풀: 표지 있는 책만 + 제목 기준 중복 제거(순서 유지).
/// 순수 함수 — 단위 테스트 대상.
List<Book> dedupAndFilterPool(List<Book> books) {
  final seen = <String>{};
  final out = <Book>[];
  for (final b in books) {
    if (b.coverUrl == null || b.coverUrl!.isEmpty) continue;
    final key = b.title.trim();
    if (key.isEmpty || seen.contains(key)) continue;
    seen.add(key);
    out.add(b);
  }
  return out;
}

/// 온보딩 완료 시 user_books 배치 insert 에 쓸 행을 만든다. 순수 함수 — 단위 테스트 대상.
///
/// 온보딩 그리드는 "읽었다"는 신호일 뿐 "좋았다"가 아니다 — 유저가 직접 고른
/// 최애(favoriteBookId)만 평가(rating='good')로 기록하고, 나머지는 미평가(null)로
/// 남겨 홈 "피드백 남겨보세요" 유도로 진짜 평가를 수집한다 (Eden 결정 2026-07-02).
/// status 는 전부 'finished' 유지 — wishlist 제약(rating 은 finished 에서만 허용) 회피.
///
/// PostgREST 배치 insert 는 모든 행의 키 집합이 동일해야 한다(PGRST102). 따라서
/// rating/emotion_tags 키는 모든 행에 두고, 최애가 아니면 null 로 채운다.
List<Map<String, dynamic>> buildOnboardingRows({
  required String userId,
  required List<String> selectedBookIds,
  String? favoriteBookId,
  List<String> favoriteEmotionTags = const [],
}) {
  return selectedBookIds.map((bid) {
    final isFavorite = bid == favoriteBookId;
    return <String, dynamic>{
      'user_id': userId,
      'book_id': bid,
      'status': 'finished', // wishlist 제약 회피 + rating 동반 가능
      'rating': isFavorite ? 'good' : null,
      'emotion_tags': (isFavorite && favoriteEmotionTags.isNotEmpty)
          ? favoriteEmotionTags
          : null,
    };
  }).toList();
}

/// 온보딩 데이터/쓰기 경로.
/// - 그리드 풀: `fallback_curation`(RLS "모두 읽기") + `books` 조인 직접 select.
/// - 완료: 선택한 책을 user_books 에 배치 쓰기(status=finished, 최애만 rating=good) →
///   현재는 최애 1권만 good 이라 DB 트리거(refresh_user_state)의 good 6권 임계로
///   즉시 tier2 로 오르진 않는다 — 홈 피드백 유도로 점진적으로 채워진다.
class OnboardingService {
  final SupabaseClient _supabase;
  OnboardingService([SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client;

  /// 온보딩 그리드용 큐레이션 풀 (rank 순, 표지 있는 책, 제목 dedup).
  Future<List<Book>> fetchCurationPool({int limit = 60}) async {
    final res = await _supabase
        .from('fallback_curation')
        .select('rank, books(*)')
        .order('rank', ascending: true)
        .limit(limit);

    final books = (res as List<dynamic>)
        .map((r) => r['books'])
        .whereType<Map<String, dynamic>>()
        .map((b) => Book.fromJson(b))
        .toList();

    return dedupAndFilterPool(books);
  }

  /// 그리드 선택 완료 → user_books 배치 쓰기.
  /// 선택 책은 이미 books 테이블에 존재(fallback_curation FK)하므로 books upsert 불필요.
  /// 최애 책에는 감성태그를 함께 기록(취향 신호 가중).
  Future<void> completeOnboarding({
    required List<String> selectedBookIds,
    String? favoriteBookId,
    List<String> favoriteEmotionTags = const [],
  }) async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) throw Exception('로그인이 필요합니다');
    if (selectedBookIds.isEmpty) return;

    final rows = buildOnboardingRows(
      userId: userId,
      selectedBookIds: selectedBookIds,
      favoriteBookId: favoriteBookId,
      favoriteEmotionTags: favoriteEmotionTags,
    );

    // 이미 서재에 있는 책과 충돌 시 갱신(멱등). user_books unique(user_id, book_id).
    await _supabase
        .from('user_books')
        .upsert(rows, onConflict: 'user_id,book_id');
  }
}
