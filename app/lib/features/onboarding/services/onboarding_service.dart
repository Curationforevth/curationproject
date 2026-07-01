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

/// 온보딩 데이터/쓰기 경로.
/// - 그리드 풀: `fallback_curation`(RLS "모두 읽기") + `books` 조인 직접 select.
/// - 완료: 선택한 책을 user_books 에 배치 쓰기(status=finished, rating=good) →
///   DB 트리거(refresh_user_state)가 good 6권 이상이면 current_tier=2 로 올려
///   온보딩 직후 /recommend("이 책 어때요?")가 작동한다.
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

    // PostgREST 배치 insert 는 모든 행의 키 집합이 동일해야 한다(PGRST102).
    // 따라서 emotion_tags 키는 모든 행에 두고, 최애가 아니면 null 로 채운다.
    final rows = selectedBookIds.map((bid) {
      return <String, dynamic>{
        'user_id': userId,
        'book_id': bid,
        'status': 'finished', // wishlist 제약 회피 + rating 동반 가능
        'rating': 'good',
        'emotion_tags': (bid == favoriteBookId && favoriteEmotionTags.isNotEmpty)
            ? favoriteEmotionTags
            : null,
      };
    }).toList();

    // 이미 서재에 있는 책과 충돌 시 갱신(멱등). user_books unique(user_id, book_id).
    await _supabase
        .from('user_books')
        .upsert(rows, onConflict: 'user_id,book_id');
  }
}
