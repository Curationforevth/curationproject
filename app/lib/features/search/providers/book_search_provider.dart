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
  final int currentPage;
  final bool hasMore;
  final bool isLoadingMore;
  final String currentQuery;

  const BookSearchState({
    this.status = BookSearchStatus.idle,
    this.results = const [],
    this.errorMessage,
    this.shelfIsbns = const {},
    this.currentPage = 1,
    this.hasMore = true,
    this.isLoadingMore = false,
    this.currentQuery = '',
  });

  BookSearchState copyWith({
    BookSearchStatus? status,
    List<Book>? results,
    String? errorMessage,
    Set<String>? shelfIsbns,
    int? currentPage,
    bool? hasMore,
    bool? isLoadingMore,
    String? currentQuery,
  }) {
    return BookSearchState(
      status: status ?? this.status,
      results: results ?? this.results,
      errorMessage: errorMessage,
      shelfIsbns: shelfIsbns ?? this.shelfIsbns,
      currentPage: currentPage ?? this.currentPage,
      hasMore: hasMore ?? this.hasMore,
      isLoadingMore: isLoadingMore ?? this.isLoadingMore,
      currentQuery: currentQuery ?? this.currentQuery,
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
    state = state.copyWith(
      status: BookSearchStatus.loading,
      currentQuery: query,
      currentPage: 1,
      hasMore: true,
    );

    try {
      final result = await _service.search(query);
      state = state.copyWith(
        status: BookSearchStatus.loaded,
        results: result.books,
        currentPage: 1,
        hasMore: !result.isEnd,
      );
    } catch (e) {
      state = state.copyWith(
        status: BookSearchStatus.error,
        errorMessage: e.toString(),
      );
    }
  }

  Future<void> loadMore() async {
    if (state.isLoadingMore || !state.hasMore || state.currentQuery.isEmpty) {
      return;
    }

    state = state.copyWith(isLoadingMore: true);

    try {
      final nextPage = state.currentPage + 1;
      final result = await _service.search(
        state.currentQuery,
        page: nextPage,
      );
      state = state.copyWith(
        results: [...state.results, ...result.books],
        currentPage: nextPage,
        hasMore: !result.isEnd,
        isLoadingMore: false,
      );
    } catch (e) {
      state = state.copyWith(isLoadingMore: false);
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
