import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/book.dart';
import '../../../core/models/user_book.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../providers/book_search_provider.dart';
import '../widgets/book_search_result_card.dart';

class BookSearchScreen extends ConsumerStatefulWidget {
  const BookSearchScreen({super.key});

  @override
  ConsumerState<BookSearchScreen> createState() => _BookSearchScreenState();
}

class _BookSearchScreenState extends ConsumerState<BookSearchScreen> {
  final _controller = TextEditingController();
  bool _isRegistering = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _showStatusBottomSheet(Book book) async {
    if (_isRegistering) return;

    final status = await showModalBottomSheet<BookStatus>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('읽기 상태 선택',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
            ListTile(
              leading: const Icon(Icons.auto_stories),
              title: const Text('읽는 중'),
              onTap: () => Navigator.pop(context, BookStatus.reading),
            ),
            ListTile(
              leading: const Icon(Icons.check_circle_outline),
              title: const Text('다 읽었어요'),
              onTap: () => Navigator.pop(context, BookStatus.read),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );

    if (status == null || !mounted) return;

    setState(() => _isRegistering = true);

    try {
      final userBookId = await addBookToShelf(ref, book, status);
      ref.read(bookSearchProvider.notifier).markAsAdded(book.isbn);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('${book.title} 서재에 추가됨'),
            action: SnackBarAction(
              label: '보러가기',
              onPressed: () => context.push('/book/$userBookId'),
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        final message = e.toString().contains('unique')
            ? '이미 서재에 있어요'
            : '추가 실패: $e';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(message)),
        );
      }
    } finally {
      if (mounted) setState(() => _isRegistering = false);
    }
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
                separatorBuilder: (context, index) =>
                    const Divider(height: 1),
                itemBuilder: (context, index) {
                  final book = searchState.results[index];
                  final isAdded = book.isbn != null &&
                      searchState.shelfIsbns.contains(book.isbn);
                  return BookSearchResultCard(
                    book: book,
                    isAdded: isAdded,
                    onTap: () => _showStatusBottomSheet(book),
                  );
                },
              ),
      },
    );
  }
}
