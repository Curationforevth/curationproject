import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/widgets/bookshelf_row.dart';
import '../providers/bookshelf_provider.dart';
import '../widgets/cover_feed_view.dart';

class BookshelfScreen extends ConsumerWidget {
  const BookshelfScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final booksAsync = ref.watch(bookshelfProvider);
    final isCoverMode = ref.watch(viewModeProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('내 서재'),
        actions: [
          // 커버/서가 토글
          _ViewToggle(
            isCoverMode: isCoverMode,
            onChanged: (value) =>
                ref.read(viewModeProvider.notifier).state = value,
          ),
          const SizedBox(width: 2),
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
            child: isCoverMode
                ? const CoverFeedView()
                : _shelfView(context, ref),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => context.push('/search'),
        child: const Icon(Icons.add),
      ),
    );
  }

  /// 기존 서가 뷰
  Widget _shelfView(BuildContext context, WidgetRef ref) {
    final booksByStatus = ref.watch(booksByStatusProvider);

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 16),
      children: [
        _buildShelfSection(
          context,
          ref: ref,
          title: '읽는 중',
          userBooks: booksByStatus[BookStatus.reading] ?? [],
        ),
        const SizedBox(height: 24),
        _buildShelfSection(
          context,
          ref: ref,
          title: '읽은 책',
          userBooks: booksByStatus[BookStatus.read] ?? [],
        ),
      ],
    );
  }

  Widget _buildShelfSection(
    BuildContext context, {
    required WidgetRef ref,
    required String title,
    required List<UserBook> userBooks,
  }) {
    final count = userBooks.where((ub) => ub.book != null).length;

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
                '$count',
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: AppColors.textSecondary,
                    ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        BookshelfRow(
          userBooks: userBooks,
          onBookTap: (userBook) {
            context.push('/book/${userBook.id}');
          },
          onReorder: (reordered) {
            reorderBooks(ref, reordered);
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

/// 커버/서가 뷰 토글 버튼
class _ViewToggle extends StatelessWidget {
  final bool isCoverMode;
  final ValueChanged<bool> onChanged;

  const _ViewToggle({
    required this.isCoverMode,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _toggleButton(
            icon: Icons.grid_view_rounded,
            isSelected: isCoverMode,
            onTap: () => onChanged(true),
          ),
          Container(width: 1, height: 14, color: AppColors.textSecondary.withValues(alpha: 0.15)),
          _toggleButton(
            icon: Icons.view_column_outlined,
            isSelected: !isCoverMode,
            onTap: () => onChanged(false),
          ),
        ],
      ),
    );
  }

  Widget _toggleButton({
    required IconData icon,
    required bool isSelected,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.all(6),
        child: Icon(
          icon,
          size: 18,
          color: isSelected
              ? AppColors.textPrimary
              : AppColors.textSecondary.withValues(alpha: 0.4),
        ),
      ),
    );
  }
}
