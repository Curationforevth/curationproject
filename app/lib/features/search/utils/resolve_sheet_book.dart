import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';

/// 검색 결과 Book(id='') 을 시트에 넘기기 전, 서재에 이미 있는 판본이면
/// 실 DB id 를 가진 서재 Book 으로 치환한다 — userBookForProvider 가 id 로
/// 매칭하므로 이 치환이 없으면 기등록 책이 '새 책'으로 보인다.
Book resolveSheetBook(WidgetRef ref, Book searchBook) {
  final isbn = searchBook.isbn;
  if (isbn == null || isbn.isEmpty) return searchBook;
  final shelf = ref.read(bookshelfProvider).valueOrNull ?? const [];
  for (final ub in shelf) {
    if (ub.book?.isbn == isbn) return ub.book!;
  }
  return searchBook;
}
