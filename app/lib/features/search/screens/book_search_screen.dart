import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/book_search_provider.dart';
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
            : ListView.separated(
                itemCount: searchState.results.length,
                separatorBuilder: (context, index) => const Divider(height: 1),
                itemBuilder: (context, index) {
                  final book = searchState.results[index];
                  return BookSearchResultCard(
                    book: book,
                    onTap: () {
                      // TODO: 서재 추가 연결
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('${book.title} 선택됨')),
                      );
                    },
                  );
                },
              ),
      },
    );
  }
}
