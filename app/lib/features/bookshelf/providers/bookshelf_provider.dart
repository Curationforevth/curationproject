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
final booksByStatusProvider =
    Provider<Map<BookStatus, List<UserBook>>>((ref) {
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
  return readBooks
      .where((ub) => ub.book != null && ub.rating == null)
      .toList();
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

final registrationServiceProvider =
    Provider<BookRegistrationService>((ref) => BookRegistrationService());

/// 서재에 책 추가. 등록된 userBookId를 반환.
Future<String> addBookToShelf(WidgetRef ref, Book book, BookStatus status) async {
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
