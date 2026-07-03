import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../home/widgets/book_detail_bottom_sheet.dart';
import '../providers/book_search_provider.dart';
import '../utils/resolve_sheet_book.dart';
import '../widgets/book_search_result_card.dart';

class BookSearchScreen extends ConsumerStatefulWidget {
  const BookSearchScreen({super.key});

  @override
  ConsumerState<BookSearchScreen> createState() => _BookSearchScreenState();
}

class _BookSearchScreenState extends ConsumerState<BookSearchScreen> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final searchState = ref.watch(bookSearchProvider);
    final shelfIsbns = ref.watch(bookshelfProvider).valueOrNull
            ?.map((ub) => ub.book?.isbn)
            .whereType<String>()
            .toSet() ??
        const <String>{};

    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: '책 제목 또는 저자 검색',
            border: InputBorder.none,
          ),
          onChanged: (query) {
            ref.read(bookSearchProvider.notifier).search(query);
          },
        ),
        actions: [
          if (_controller.text.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.clear),
              onPressed: () {
                _controller.clear();
                ref.read(bookSearchProvider.notifier).clear();
              },
            ),
        ],
      ),
      body: switch (searchState.status) {
        BookSearchStatus.idle => const Center(
            child: Text('책을 검색해보세요'),
          ),
        BookSearchStatus.loading => const Center(
            child: CircularProgressIndicator(),
          ),
        BookSearchStatus.error => Center(
            child: Text('검색 실패: ${searchState.errorMessage}'),
          ),
        BookSearchStatus.loaded => searchState.results.isEmpty
            ? const Center(child: Text('검색 결과가 없습니다'))
            : ListView.builder(
                itemCount: searchState.results.length +
                    (searchState.hasMore ? 1 : 0),
                itemBuilder: (context, index) {
                  // 하단 로딩 인디케이터
                  if (index == searchState.results.length) {
                    return const Padding(
                      padding: EdgeInsets.all(24),
                      child: Center(child: CircularProgressIndicator()),
                    );
                  }

                  // 끝에서 4개 전에 다음 페이지 로드
                  if (index == searchState.results.length - 4 &&
                      searchState.hasMore &&
                      !searchState.isLoadingMore) {
                    ref.read(bookSearchProvider.notifier).loadMore();
                  }

                  final book = searchState.results[index];
                  final isAdded = book.isbn != null &&
                      (searchState.shelfIsbns.contains(book.isbn) ||
                          shelfIsbns.contains(book.isbn));
                  return Column(
                    children: [
                      BookSearchResultCard(
                        book: book,
                        isAdded: isAdded,
                        onTap: () => BookDetailBottomSheet.show(
                            context, resolveSheetBook(ref, book)),
                      ),
                      if (index < searchState.results.length - 1)
                        const Divider(height: 1),
                    ],
                  );
                },
              ),
      },
    );
  }
}
