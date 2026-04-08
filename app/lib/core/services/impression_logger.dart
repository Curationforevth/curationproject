import 'package:supabase_flutter/supabase_flutter.dart';

/// 추천 노출/액션을 recommendation_impressions 테이블에 비동기 로깅.
/// 모든 메서드는 fire-and-forget — 실패해도 UI 응답을 막지 않는다.
class ImpressionLogger {
  ImpressionLogger(this._client);
  final SupabaseClient _client;

  static const _validActions = {
    'clicked',
    'saved',
    'liked',
    'disliked',
    'ignored',
  };

  /// 노출 row 빌드 (테스트 가능, Supabase 호출 X)
  static List<Map<String, dynamic>> buildImpressionRows({
    required String userId,
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) {
    return [
      for (var i = 0; i < bookIds.length; i++)
        {
          'user_id': userId,
          'book_id': bookIds[i],
          'position': i,
          'source': source,
          'algorithm_version': algorithmVersion,
          if (sessionId != null) 'session_id': sessionId,
        },
    ];
  }

  static Map<String, dynamic> buildActionUpdate({required String action}) {
    if (!_validActions.contains(action)) {
      throw ArgumentError.value(action, 'action', 'invalid action');
    }
    return {
      'action': action,
      'action_at': DateTime.now().toUtc().toIso8601String(),
    };
  }

  /// 추천 카드 N개 노출 로깅 (fire-and-forget)
  Future<void> logImpressions({
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) async {
    final userId = _client.auth.currentUser?.id;
    if (userId == null || bookIds.isEmpty) return;
    final rows = buildImpressionRows(
      userId: userId,
      bookIds: bookIds,
      source: source,
      algorithmVersion: algorithmVersion,
      sessionId: sessionId,
    );
    try {
      await _client.from('recommendation_impressions').insert(rows);
    } catch (_) {
      // 비동기 로깅 — 실패는 무시 (스펙 §5)
    }
  }

  /// 가장 최근 노출 row 의 action 업데이트.
  /// (user_id, book_id) 기준 가장 최근 unactioned row 1건만 갱신.
  Future<void> logAction({
    required String bookId,
    required String action,
  }) async {
    final userId = _client.auth.currentUser?.id;
    if (userId == null) return;
    final patch = buildActionUpdate(action: action);
    try {
      final latest = await _client
          .from('recommendation_impressions')
          .select('id')
          .eq('user_id', userId)
          .eq('book_id', bookId)
          .filter('action', 'is', null)
          .order('shown_at', ascending: false)
          .limit(1)
          .maybeSingle();
      if (latest == null) return;
      await _client
          .from('recommendation_impressions')
          .update(patch)
          .eq('id', latest['id']);
    } catch (_) {
      // ignore
    }
  }
}
