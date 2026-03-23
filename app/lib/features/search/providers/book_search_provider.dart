import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../../core/services/book_search_service.dart';
import '../../../core/services/book_registration_service.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';

final bookSearchServiceProvider =
    Provider<BookSearchService>((ref) => BookSearchService());

final bookSearchProvider =
    StateNotifierProvider<BookSearchNotifier, BookSearchState>((ref) {
  return BookSearchNotifier(
    ref.watch(bookSearchServiceProvider),
    ref.watch(registrationServiceProvider),
  );
});

enum BookSearchStatus { idle, loading, loaded, error }

class BookSearchState {
  final BookSearchStatus status;
  final List<Book> results;
  final String? errorMessage;
  final Set<String> shelfIsbns;

  const BookSearchState({
    this.status = BookSearchStatus.idle,
    this.results = const [],
    this.errorMessage,
    this.shelfIsbns = const {},
  });

  BookSearchState copyWith({
    BookSearchStatus? status,
    List<Book>? results,
    String? errorMessage,
    Set<String>? shelfIsbns,
  }) {
    return BookSearchState(
      status: status ?? this.status,
      results: results ?? this.results,
      errorMessage: errorMessage,
      shelfIsbns: shelfIsbns ?? this.shelfIsbns,
    );
  }
}

class BookSearchNotifier extends StateNotifier<BookSearchState> {
  final BookSearchService _service;
  final BookRegistrationService _registrationService;
  Timer? _debounce;

  BookSearchNotifier(this._service, this._registrationService)
      : super(const BookSearchState()) {
    _loadShelfIsbns();
  }

  Future<void> _loadShelfIsbns() async {
    try {
      final isbns = await _registrationService.getShelfIsbns();
      state = state.copyWith(shelfIsbns: isbns);
    } catch (_) {}
  }

  void markAsAdded(String? isbn) {
    if (isbn == null || isbn.isEmpty) return;
    state = state.copyWith(shelfIsbns: {...state.shelfIsbns, isbn});
  }

  void search(String query) {
    _debounce?.cancel();

    if (query.trim().isEmpty) {
      state = BookSearchState(shelfIsbns: state.shelfIsbns);
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
      state = state.copyWith(
        status: BookSearchStatus.loaded,
        results: results,
      );
    } catch (e) {
      state = state.copyWith(
        status: BookSearchStatus.error,
        errorMessage: e.toString(),
      );
    }
  }

  void clear() {
    _debounce?.cancel();
    state = BookSearchState(shelfIsbns: state.shelfIsbns);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }
}
