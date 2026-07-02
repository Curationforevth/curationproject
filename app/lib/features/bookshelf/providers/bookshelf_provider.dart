import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/book_registration_service.dart';
import '../../../core/models/book.dart';
import '../../home/providers/recommendation_provider.dart';

/// 유저 서재 데이터 (Supabase에서 user_books + books join)
final bookshelfProvider = FutureProvider<List<UserBook>>((ref) async {
  final supabase = Supabase.instance.client;
  final userId = supabase.auth.currentUser?.id;
  if (userId == null) return [];

  final response = await supabase
      .from('user_books')
      .select('*, books(*)')
      .eq('user_id', userId)
      .order('shelf_order', ascending: true, nullsFirst: false)
      .order('created_at', ascending: false);

  return (response as List<dynamic>)
      .map((json) => UserBook.fromJson(json as Map<String, dynamic>))
      .toList();
});

/// 상태별 그룹핑
final booksByStatusProvider = Provider<Map<BookStatus, List<UserBook>>>((ref) {
  final booksAsync = ref.watch(bookshelfProvider);
  final books = booksAsync.valueOrNull ?? [];

  return {
    for (final status in BookStatus.values)
      status: books.where((ub) => ub.status == status).toList(),
  };
});

/// 총 책 수 (마일스톤용)
final bookCountProvider = Provider<int>((ref) {
  final booksAsync = ref.watch(bookshelfProvider);
  return booksAsync.valueOrNull?.length ?? 0;
});

/// 피드백 미작성 책 (CTA용) — read 상태이고 rating이 없는 책
final unreviewedBooksProvider = Provider<List<UserBook>>((ref) {
  final byStatus = ref.watch(booksByStatusProvider);
  final readBooks = byStatus[BookStatus.read] ?? [];
  return readBooks.where((ub) => ub.book != null && ub.rating == null).toList();
});

/// 작가별 그룹핑 (2권+ 필터)
final authorGroupsProvider = Provider<Map<String, List<UserBook>>>((ref) {
  final books = ref.watch(bookshelfProvider).valueOrNull ?? [];
  final grouped = <String, List<UserBook>>{};
  for (final ub in books) {
    final author = ub.book?.author;
    if (author != null && author.isNotEmpty) {
      // 첫 번째 저자만 (콤마 분리 + 괄호 역할 표기 제거)
      var primary = author.split(',').first.trim();
      primary = primary.replaceAll(RegExp(r'\s*\(.*?\)'), '').trim();
      if (primary.isNotEmpty) {
        grouped.putIfAbsent(primary, () => []).add(ub);
      }
    }
  }
  // 2권 이상만 반환
  grouped.removeWhere((_, books) => books.length < 2);
  return grouped;
});

/// 서재에서 특정 book_id 의 UserBook 조회(없으면 null).
///
/// 홈 진입 시 이미 로드된 bookshelfProvider 를 재사용 — 추가 네트워크 0.
/// 로딩/에러 중엔 null 을 반환해 호출측이 "새 책" UI 로 폴백한다(잘못돼도
/// 데이터 계층 resolveShelfWrite 가 전이/no-op 로 안전).
final userBookForProvider = Provider.family<UserBook?, String>((ref, bookId) {
  final books = ref.watch(bookshelfProvider).valueOrNull;
  if (books == null) return null;
  for (final ub in books) {
    if (ub.bookId == bookId) return ub;
  }
  return null;
});

final registrationServiceProvider = Provider<BookRegistrationService>(
  (ref) => BookRegistrationService(),
);

/// 서재에 책 추가. 등록된 userBookId를 반환.
Future<String> addBookToShelf(
  WidgetRef ref,
  Book book,
  BookStatus status,
) async {
  final service = ref.read(registrationServiceProvider);
  final userBookId = await service.registerBook(book, status);
  ref.invalidate(bookshelfProvider);
  // 서재가 바뀌었으니 서버가 추천을 **선제 재계산**하게 fire-and-forget 트리거
  // → 유저가 추천을 열 땐 캐시가 warm(계산을 읽기 경로 밖으로). await 하지 않는다.
  unawaited(ref.read(recommendationServiceProvider).triggerRecompute());
  return userBookId;
}

/// 서재 책 순서 변경 (shelf_order batch update)
Future<void> reorderBooks(WidgetRef ref, List<UserBook> reordered) async {
  final supabase = Supabase.instance.client;
  for (int i = 0; i < reordered.length; i++) {
    await supabase
        .from('user_books')
        .update({'shelf_order': i})
        .eq('id', reordered[i].id);
  }
  ref.invalidate(bookshelfProvider);
}

// ---------------------------------------------------------------------------
// 서재 삭제 + 실행 취소 (설계:
// docs/superpowers/specs/2026-07-02-shelf-remove-not-interested-design.md)
// ---------------------------------------------------------------------------

/// 삭제 전 UserBook 행 전체(id 포함)를 스냅숏 Map 으로 뜬다 — 실행 취소 시
/// 그대로 재 INSERT 하기 위함. UserBook.toJson() 은 id 를 뺀 upsert-only 형태라
/// 별도로 둔다(순수 함수 — 테스트 가능).
Map<String, dynamic> userBookSnapshot(UserBook ub) {
  return {
    'id': ub.id,
    'user_id': ub.userId,
    'book_id': ub.bookId,
    'status': ub.status.toJson(),
    'shelf_order': ub.shelfOrder,
    'rating': ub.rating,
    'emotion_tags': ub.emotionTags,
    'review_text': ub.reviewText,
  };
}

/// 스냅숏으로부터 재삽입용 payload 를 만든다. [includeId] 가 false 면 id 를 빼
/// DB 가 새 id 를 발급하게 한다(23505 폴백 — 원래 id 가 이미 다른 행에 쓰였을 때).
Map<String, dynamic> restoreInsertPayload(
  Map<String, dynamic> snapshot, {
  required bool includeId,
}) {
  final payload = Map<String, dynamic>.from(snapshot);
  if (!includeId) payload.remove('id');
  return payload;
}

/// user_books 행 DELETE. 삭제 전 스냅숏(모든 컬럼)을 반환 — 실행 취소 복원용.
///
/// WidgetRef 가 아니라 ProviderContainer 를 받는다: 호출측(바텀시트)이 pop 된
/// 뒤에도(2초 지연 invalidate, 스낵바 실행 취소) 동작해야 하는데, 폐기된 위젯의
/// ref 는 StateError 를 던진다. container 는 위젯 수명과 무관.
Future<Map<String, dynamic>> removeFromShelf(
  ProviderContainer container,
  UserBook userBook,
) async {
  final supabase = Supabase.instance.client;
  final snapshot = await removeFromShelfWith(supabase, userBook);
  container.invalidate(bookshelfProvider);
  // 서재가 바뀌었으니 서버가 추천을 선제 재계산하게 fire-and-forget 트리거
  // (addBookToShelf 와 동일 패턴). await 하지 않는다.
  unawaited(container.read(recommendationServiceProvider).triggerRecompute());
  // 추천 섹션만 2초 지연 후 재조회 — feedback_flow_provider.dart submit() 의
  // 지연 invalidate 패턴과 동일(recompute 가 서버에 computing 플래그를 세울
  // 시간을 준다). computing=true 응답은 _RecommendationPoller 가 폴링한다.
  unawaited(
    Future<void>.delayed(const Duration(seconds: 2)).then((_) {
      container.invalidate(recommendationsProvider);
    }),
  );
  return snapshot;
}

/// removeFromShelf 의 Supabase 호출부만 분리 — 테스트에서 fake client 주입용.
Future<Map<String, dynamic>> removeFromShelfWith(
  SupabaseClient client,
  UserBook userBook,
) async {
  final snapshot = userBookSnapshot(userBook);
  await client.from('user_books').delete().eq('id', userBook.id);
  return snapshot;
}

/// 삭제 전 스냅숏 그대로 user_books 행을 복원(실행 취소). id 재사용을 먼저
/// 시도하고, 23505(이미 다른 행이 그 id 를 씀)면 id 를 빼고 재삽입한다.
Future<void> restoreToShelf(
  ProviderContainer container,
  Map<String, dynamic> snapshot,
) async {
  final supabase = Supabase.instance.client;
  await restoreToShelfWith(supabase, snapshot);
  container.invalidate(bookshelfProvider);
  unawaited(container.read(recommendationServiceProvider).triggerRecompute());
  unawaited(
    Future<void>.delayed(const Duration(seconds: 2)).then((_) {
      container.invalidate(recommendationsProvider);
    }),
  );
}

/// restoreToShelf 의 Supabase 호출부만 분리 — 테스트에서 fake client 주입용.
Future<void> restoreToShelfWith(
  SupabaseClient client,
  Map<String, dynamic> snapshot,
) async {
  try {
    await client
        .from('user_books')
        .insert(restoreInsertPayload(snapshot, includeId: true));
  } on PostgrestException catch (e) {
    if (e.code == '23505') {
      await client
          .from('user_books')
          .insert(restoreInsertPayload(snapshot, includeId: false));
    } else {
      rethrow;
    }
  }
}
