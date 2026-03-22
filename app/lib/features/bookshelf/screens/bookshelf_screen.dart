import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/widgets/bookshelf_row.dart';
import '../providers/bookshelf_provider.dart';

class BookshelfScreen extends ConsumerWidget {
  const BookshelfScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final booksAsync = ref.watch(bookshelfProvider);
    final booksByStatus = ref.watch(booksByStatusProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('내 서재'),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            onPressed: () => context.push('/search'),
          ),
        ],
      ),
      body: booksAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, stack) => Center(
          child: Text('서재를 불러오지 못했습니다: $error'),
        ),
        data: (books) {
          if (books.isEmpty) return _emptyState(context);

          return RefreshIndicator(
            onRefresh: () => ref.refresh(bookshelfProvider.future),
            child: ListView(
              padding: const EdgeInsets.symmetric(vertical: 16),
              children: [
                _buildSection(
                  context,
                  title: '읽는 중',
                  books: booksByStatus[BookStatus.reading] ?? [],
                ),
                const SizedBox(height: 24),
                _buildSection(
                  context,
                  title: '읽은 책',
                  books: booksByStatus[BookStatus.read] ?? [],
                ),
                const SizedBox(height: 24),
                _buildSection(
                  context,
                  title: '읽고 싶은 책',
                  books: booksByStatus[BookStatus.wantToRead] ?? [],
                ),
              ],
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.push('/search'),
        child: const Icon(Icons.add),
      ),
    );
  }

  Widget _buildSection(
    BuildContext context, {
    required String title,
    required List<UserBook> books,
  }) {
    final booksWithData =
        books.where((ub) => ub.book != null).map((ub) => ub.book!).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Row(
            children: [
              Text(
                title,
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: AppColors.textPrimary,
                      fontWeight: FontWeight.w600,
                    ),
              ),
              const SizedBox(width: 8),
              Text(
                '${booksWithData.length}',
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: AppColors.textSecondary,
                    ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        BookshelfRow(
          books: booksWithData,
          onBookTap: (book) {
            // TODO: 책 상세 화면으로 이동
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(book.title)),
            );
          },
        ),
      ],
    );
  }

  Widget _emptyState(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.menu_book_rounded,
            size: 64,
            color: AppColors.textSecondary.withValues(alpha: 0.4),
          ),
          const SizedBox(height: 16),
          Text(
            '아직 서재가 비어있어요',
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: AppColors.textSecondary,
                ),
          ),
          const SizedBox(height: 8),
          Text(
            '책을 검색해서 서재에 추가해보세요',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: AppColors.textSecondary,
                ),
          ),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: () => context.push('/search'),
            icon: const Icon(Icons.search),
            label: const Text('책 검색하기'),
          ),
        ],
      ),
    );
  }
}
