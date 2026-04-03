import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/user_book.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';

/// Combines reading + unreviewed books, max 5, reading first
final myBooksProvider =
    Provider<List<({UserBook userBook, String ctaType})>>((ref) {
  final byStatus = ref.watch(booksByStatusProvider);
  final unreviewed = ref.watch(unreviewedBooksProvider);

  final result = <({UserBook userBook, String ctaType})>[];

  // reading books first
  for (final ub in byStatus[BookStatus.reading] ?? []) {
    if (ub.book != null) {
      result.add((userBook: ub, ctaType: 'reading'));
    }
  }
  // then unreviewed
  for (final ub in unreviewed) {
    if (result.length >= 5) break;
    if (ub.book != null) {
      result.add((userBook: ub, ctaType: 'needsFeedback'));
    }
  }

  return result.take(5).toList();
});

/// Want to read books
final wishlistBooksProvider = Provider<List<UserBook>>((ref) {
  final byStatus = ref.watch(booksByStatusProvider);
  return byStatus[BookStatus.wantToRead] ?? [];
});
