import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/book_registration_service.dart';
import '../../../core/models/book.dart';

/// 유저 서재 데이터 (Supabase에서 user_books + books join)
final bookshelfProvider = FutureProvider<List<UserBook>>((ref) async {
  final supabase = Supabase.instance.client;
  final userId = supabase.auth.currentUser?.id;
  if (userId == null) return [];

  final response = await supabase
      .from('user_books')
      .select('*, books(*)')
      .eq('user_id', userId)
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

final registrationServiceProvider =
    Provider<BookRegistrationService>((ref) => BookRegistrationService());

/// 서재에 책 추가 (등록 파이프라인 실행 + 리프레시)
Future<void> addBookToShelf(WidgetRef ref, Book book, BookStatus status) async {
  final service = ref.read(registrationServiceProvider);
  await service.registerBook(book, status);
  ref.invalidate(bookshelfProvider);
}
