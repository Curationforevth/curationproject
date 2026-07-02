import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/widgets/bookshelf_row.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../../core/utils/author_format.dart';

class LibraryScreen extends ConsumerWidget {
  const LibraryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final booksAsync = ref.watch(bookshelfProvider);

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        child: booksAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => Center(
            child: Text('서재를 불러오지 못했습니다: $error'),
          ),
          data: (books) {
            if (books.isEmpty) return _EmptyState();

            return RefreshIndicator(
              onRefresh: () => ref.refresh(bookshelfProvider.future),
              child: _LibraryContent(ref: ref),
            );
          },
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Main content
// ---------------------------------------------------------------------------

class _LibraryContent extends ConsumerWidget {
  final WidgetRef ref;

  const _LibraryContent({required this.ref});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final booksByStatus = ref.watch(booksByStatusProvider);
    final readBooks =
        (booksByStatus[BookStatus.read] ?? []).where((ub) => ub.book != null).toList();
    final readingBooks =
        (booksByStatus[BookStatus.reading] ?? []).where((ub) => ub.book != null).toList();
    final readCount = readBooks.length;
    final readingCount = readingBooks.length;

    return CustomScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      slivers: [
        // ── Top bar ──────────────────────────────────────────────────────
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Text(
                  '내 서재',
                  style: Theme.of(context).textTheme.headlineLarge?.copyWith(
                        fontSize: 26,
                        fontWeight: FontWeight.w300,
                        letterSpacing: -1.0,
                        color: AppColors.textPrimary,
                      ),
                ),
                const Spacer(),
                ElevatedButton(
                  onPressed: () => context.push('/register'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 10,
                    ),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    elevation: 0,
                  ),
                  child: const Text(
                    '+ 추가',
                    style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
                  ),
                ),
              ],
            ),
          ),
        ),

        // ── Stats row ────────────────────────────────────────────────────
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 10, 20, 20),
            child: _StatsRow(readCount: readCount, readingCount: readingCount),
          ),
        ),

        // ── "읽는 중" section ─────────────────────────────────────────────
        if (readingBooks.isNotEmpty) ...[
          SliverToBoxAdapter(
            child: _ReadingSectionHeader(count: readingCount),
          ),
          SliverToBoxAdapter(
            child: _ReadingCardRow(readingBooks: readingBooks),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 28)),
        ],

        // ── "읽은 책" shelf ───────────────────────────────────────────────
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.only(left: 20, bottom: 10),
            child: Text(
              '읽은 책',
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w500,
                color: AppColors.textSecondary,
                letterSpacing: 0.3,
              ),
            ),
          ),
        ),
        SliverToBoxAdapter(
          child: BookshelfRow(
            userBooks: readBooks,
            onBookTap: (userBook) {
              context.push('/book/${userBook.id}');
            },
            onReorder: (reordered) {
              reorderBooks(ref, reordered);
            },
          ),
        ),

        const SliverToBoxAdapter(child: SizedBox(height: 32)),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Stats row
// ---------------------------------------------------------------------------

class _StatsRow extends StatelessWidget {
  final int readCount;
  final int readingCount;

  const _StatsRow({required this.readCount, required this.readingCount});

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).textTheme.bodyMedium?.copyWith(
          color: AppColors.textSecondary,
        );
    final bold = base?.copyWith(fontWeight: FontWeight.bold);

    return Text.rich(
      TextSpan(
        children: [
          TextSpan(text: '읽은 책 ', style: base),
          TextSpan(text: '$readCount', style: bold),
          TextSpan(text: '  읽는 중 ', style: base),
          TextSpan(text: '$readingCount', style: bold),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Reading section header
// ---------------------------------------------------------------------------

class _ReadingSectionHeader extends StatelessWidget {
  final int count;

  const _ReadingSectionHeader({required this.count});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      child: Row(
        children: [
          Text(
            '읽는 중',
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w600,
                ),
          ),
          const SizedBox(width: 6),
          Text(
            '$count',
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
                  color: AppColors.textSecondary,
                ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Horizontal reading card row
// ---------------------------------------------------------------------------

class _ReadingCardRow extends StatelessWidget {
  final List<UserBook> readingBooks;

  const _ReadingCardRow({required this.readingBooks});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 98,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        itemCount: readingBooks.length,
        separatorBuilder: (context, index) => const SizedBox(width: 10),
        itemBuilder: (context, index) {
          return _ReadingCard(userBook: readingBooks[index]);
        },
      ),
    );
  }
}

class _ReadingCard extends ConsumerWidget {
  final UserBook userBook;

  const _ReadingCard({required this.userBook});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final book = userBook.book!;

    return Container(
      width: 280,
      decoration: BoxDecoration(
        color: const Color(0xFFFAFAFA),
        borderRadius: BorderRadius.circular(14),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          // Cover image
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: book.coverUrl != null
                ? Image.network(
                    book.coverUrl!,
                    width: 44,
                    height: 64,
                    fit: BoxFit.cover,
                    errorBuilder: (context, error, stackTrace) => _coverFallback(),
                  )
                : _coverFallback(),
          ),
          const SizedBox(width: 12),
          // Title + author + button
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  book.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: AppColors.textPrimary,
                  ),
                ),
                if (book.author != null && book.author!.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    displayAuthor(book.author),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 11,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                _DoneReadingButton(userBookId: userBook.id, ref: ref),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _coverFallback() {
    return Container(
      width: 44,
      height: 64,
      decoration: BoxDecoration(
        color: AppColors.border,
        borderRadius: BorderRadius.circular(6),
      ),
      child: const Icon(
        Icons.book_outlined,
        size: 20,
        color: AppColors.textSecondary,
      ),
    );
  }
}

class _DoneReadingButton extends StatelessWidget {
  final String userBookId;
  final WidgetRef ref;

  const _DoneReadingButton({required this.userBookId, required this.ref});

  Future<void> _onTap(BuildContext context) async {
    try {
      final supabase = Supabase.instance.client;
      await supabase
          .from('user_books')
          .update({'status': BookStatus.read.toJson()})
          .eq('id', userBookId);
      ref.invalidate(bookshelfProvider);
      if (context.mounted) {
        context.push('/feedback/$userBookId');
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('오류가 발생했어요: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => _onTap(context),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: AppColors.primary,
          borderRadius: BorderRadius.circular(999),
        ),
        child: const Text(
          '다 읽었어요',
          style: TextStyle(
            fontSize: 11,
            color: Colors.white,
            fontWeight: FontWeight.w500,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

class _EmptyState extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
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
              '책을 검색해서 추가해보세요',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: AppColors.textSecondary,
                  ),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => context.push('/register'),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.primary,
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
                elevation: 0,
              ),
              child: const Text('책 추가하기'),
            ),
          ],
        ),
      ),
    );
  }
}
