import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/book.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/impression_logger.dart';
import '../../../core/services/recommendation_service.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../providers/recommendation_provider.dart';

/// 책 상세 바텀시트 — 커버 피드에서 탭했을 때 표시
class BookDetailBottomSheet extends ConsumerStatefulWidget {
  final Book book;

  const BookDetailBottomSheet({super.key, required this.book});

  static Future<void> show(BuildContext context, Book book) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => BookDetailBottomSheet(book: book),
    );
  }

  @override
  ConsumerState<BookDetailBottomSheet> createState() =>
      _BookDetailBottomSheetState();
}

class _BookDetailBottomSheetState
    extends ConsumerState<BookDetailBottomSheet> {
  bool _bookmarked = false;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    unawaited(
      ImpressionLogger(Supabase.instance.client)
          .logAction(bookId: widget.book.id, action: 'clicked'),
    );
  }

  Future<void> _handleReading() async {
    if (_isLoading) return;
    setState(() => _isLoading = true);
    try {
      await addBookToShelf(ref, widget.book, BookStatus.reading);
      if (mounted) {
        Navigator.of(context).pop();
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('읽는 중으로 추가했어요')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('오류가 발생했어요: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _handleRead() async {
    if (_isLoading) return;
    setState(() => _isLoading = true);
    try {
      final userBookId =
          await addBookToShelf(ref, widget.book, BookStatus.read);
      if (mounted) {
        Navigator.of(context).pop();
        context.push('/feedback/$userBookId');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('오류가 발생했어요: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _handleBookmark() async {
    if (_isLoading) return;
    final wasBookmarked = _bookmarked;
    setState(() {
      _bookmarked = !_bookmarked;
      _isLoading = true;
    });
    try {
      await addBookToShelf(ref, widget.book, BookStatus.wantToRead);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('읽고싶은 책에 추가했어요')),
        );
      }
    } catch (e) {
      // revert on error
      if (mounted) {
        setState(() => _bookmarked = wasBookmarked);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('오류가 발생했어요: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final book = widget.book;

    return Container(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 드래그 핸들
          const SizedBox(height: 12),
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.border,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 24),

          // 책 정보
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 표지
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: SizedBox(
                    width: 80,
                    height: 116,
                    child: book.coverUrl != null
                        ? Image.network(
                            book.coverUrl!,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) =>
                                _buildCoverPlaceholder(),
                          )
                        : _buildCoverPlaceholder(),
                  ),
                ),
                const SizedBox(width: 16),
                // 텍스트 정보
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        book.title,
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w500,
                          color: AppColors.textPrimary,
                          height: 1.3,
                        ),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 4),
                      if (book.author != null && book.author!.isNotEmpty)
                        Text(
                          book.author!,
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w300,
                            color: AppColors.textSecondary,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),

          // 설명
          if (book.description != null && book.description!.isNotEmpty) ...[
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Text(
                book.description!,
                style: const TextStyle(
                  fontSize: 13,
                  color: Color(0xFF64748B),
                  height: 1.5,
                ),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],

          const SizedBox(height: 24),

          // 액션 버튼 3개
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Row(
              children: [
                // 읽는 중
                Expanded(
                  child: _ActionButton(
                    label: '읽는 중',
                    isPrimary: false,
                    isLoading: _isLoading,
                    onTap: _handleReading,
                  ),
                ),
                const SizedBox(width: 8),
                // 읽었어요
                Expanded(
                  child: _ActionButton(
                    label: '읽었어요',
                    isPrimary: true,
                    isLoading: _isLoading,
                    onTap: _handleRead,
                  ),
                ),
                const SizedBox(width: 8),
                // 북마크
                _BookmarkButton(
                  bookmarked: _bookmarked,
                  isLoading: _isLoading,
                  onTap: _handleBookmark,
                ),
              ],
            ),
          ),

          // 비슷한 책 섹션
          _SimilarBooksSection(bookId: book.id),

          // 하단 안전 여백
          SizedBox(height: MediaQuery.of(context).padding.bottom + 24),
        ],
      ),
    );
  }

  Widget _buildCoverPlaceholder() {
    return Container(
      color: AppColors.shelf,
      child: const Icon(
        Icons.menu_book,
        color: AppColors.textSecondary,
        size: 32,
      ),
    );
  }
}

class _ActionButton extends StatelessWidget {
  final String label;
  final bool isPrimary;
  final bool isLoading;
  final VoidCallback onTap;

  const _ActionButton({
    required this.label,
    required this.isPrimary,
    required this.isLoading,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: isLoading ? null : onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: isPrimary ? AppColors.primary : const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(10),
          border: isPrimary
              ? null
              : Border.all(color: const Color(0xFFF1F5F9)),
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: isPrimary ? AppColors.textOnPrimary : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 비슷한 책 섹션
// ---------------------------------------------------------------------------

class _SimilarBooksSection extends ConsumerWidget {
  final String bookId;

  const _SimilarBooksSection({required this.bookId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // book_id가 비어있으면 (예: 추천 서버에서 온 임시 ID가 아직 없으면) 숨김
    if (bookId.isEmpty) return const SizedBox.shrink();

    final similarAsync = ref.watch(similarBooksProvider(bookId));

    return similarAsync.when(
      loading: () => const Padding(
        padding: EdgeInsets.symmetric(vertical: 16),
        child: Center(
          child: SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: AppColors.textSecondary,
            ),
          ),
        ),
      ),
      error: (_, __) => const SizedBox.shrink(),
      data: (books) {
        if (books.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(24, 24, 24, 10),
              child: Text(
                '비슷한 책',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                  letterSpacing: 0.01,
                ),
              ),
            ),
            SizedBox(
              height: 124,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 24),
                itemCount: books.length,
                separatorBuilder: (_, __) => const SizedBox(width: 10),
                itemBuilder: (context, index) {
                  final similar = books[index];
                  return _SimilarBookCard(
                    book: similar,
                    onTap: () {
                      Navigator.pop(context);
                      BookDetailBottomSheet.show(context, similar.toBook());
                    },
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

class _SimilarBookCard extends StatelessWidget {
  final RecommendedBook book;
  final VoidCallback onTap;

  const _SimilarBookCard({required this.book, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
      width: 72,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              width: 72,
              height: 104,
              child: book.coverUrl != null
                  ? Image.network(
                      book.coverUrl!,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => _SimilarCoverFallback(),
                    )
                  : _SimilarCoverFallback(),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            book.title,
            style: const TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w500,
              color: AppColors.textPrimary,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
      ),
    );
  }
}

class _SimilarCoverFallback extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.shelf,
      child: const Icon(
        Icons.menu_book,
        color: AppColors.textSecondary,
        size: 20,
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _BookmarkButton extends StatelessWidget {
  final bool bookmarked;
  final bool isLoading;
  final VoidCallback onTap;

  const _BookmarkButton({
    required this.bookmarked,
    required this.isLoading,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: isLoading ? null : onTap,
      child: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          color: const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: const Color(0xFFF1F5F9)),
        ),
        alignment: Alignment.center,
        child: Icon(
          bookmarked ? Icons.bookmark : Icons.bookmark_border,
          color: AppColors.textPrimary,
          size: 20,
        ),
      ),
    );
  }
}
