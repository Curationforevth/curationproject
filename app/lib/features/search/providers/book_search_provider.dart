import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../../core/services/book_search_service.dart';

final bookSearchServiceProvider =
    Provider<BookSearchService>((ref) => BookSearchService());

final bookSearchProvider =
    StateNotifierProvider<BookSearchNotifier, BookSearchState>((ref) {
  return BookSearchNotifier(ref.watch(bookSearchServiceProvider));
});

enum BookSearchStatus { idle, loading, loaded, error }

class BookSearchState {
  final BookSearchStatus status;
  final List<Book> results;
  final String? errorMessage;

  const BookSearchState({
    this.status = BookSearchStatus.idle,
    this.results = const [],
    this.errorMessage,
  });

  BookSearchState copyWith({
    BookSearchStatus? status,
    List<Book>? results,
    String? errorMessage,
  }) {
    return BookSearchState(
      status: status ?? this.status,
      results: results ?? this.results,
      errorMessage: errorMessage,
    );
  }
}

class BookSearchNotifier extends StateNotifier<BookSearchState> {
  final BookSearchService _service;
  Timer? _debounce;

  BookSearchNotifier(this._service) : super(const BookSearchState());

  void search(String query) {
    _debounce?.cancel();

    if (query.trim().isEmpty) {
      state = const BookSearchState();
      return;
    }

    _debounce = Timer(const Duration(milliseconds: 500), () {
      _performSearch(query);
    });
  }

  Future<void> _performSearch(String query) async {
    state = state.copyWith(status: BookSearchStatus.loading);

    try {
      final results = await _service.search(query);
      state = BookSearchState(
        status: BookSearchStatus.loaded,
        results: results,
      );
    } catch (e) {
      state = BookSearchState(
        status: BookSearchStatus.error,
        errorMessage: e.toString(),
      );
    }
  }

  void clear() {
    _debounce?.cancel();
    state = const BookSearchState();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }
}
