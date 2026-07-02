import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../providers/book_detail_provider.dart';
import '../widgets/rating_selector.dart';
import '../widgets/emotion_tag_chips.dart';
import '../widgets/review_text_section.dart';
import '../../../core/utils/author_format.dart';

class BookDetailScreen extends ConsumerWidget {
  final String userBookId;

  const BookDetailScreen({super.key, required this.userBookId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detailState = ref.watch(bookDetailProvider(userBookId));
    final emotionTagsAsync = ref.watch(emotionTagOptionsProvider);
    final promptsAsync = ref.watch(reflectionPromptsProvider);

    if (detailState.isLoading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    if (detailState.error != null || detailState.userBook == null) {
      return Scaffold(
        appBar: AppBar(),
        body: Center(
          child: Text('불러오기 실패: ${detailState.error ?? "알 수 없는 오류"}'),
        ),
      );
    }

    final userBook = detailState.userBook!;
    final book = userBook.book;

    return Scaffold(
      appBar: AppBar(
        title: Text(book?.title ?? ''),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. 책 정보 (컴팩트)
            _BookInfoHeader(
              userBook: userBook,
              onStatusChange: () => _showStatusBottomSheet(context, ref, userBook),
            ),

            const SizedBox(height: 28),

            // 2. 호오 평가
            RatingSelector(
              currentRating: userBook.rating,
              disabled: detailState.isSaving,
              onChanged: (rating) async {
                try {
                  await ref
                      .read(bookDetailProvider(userBookId).notifier)
                      .updateRating(rating);
                } catch (_) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                    );
                  }
                }
              },
            ),

            const SizedBox(height: 28),

            // 3. 감성 태그
            emotionTagsAsync.when(
              data: (tags) => EmotionTagChips(
                options: tags,
                selectedIds: userBook.emotionTags ?? [],
                disabled: detailState.isSaving,
                onToggle: (tagId) async {
                  try {
                    await ref
                        .read(bookDetailProvider(userBookId).notifier)
                        .toggleEmotionTag(tagId);
                  } catch (_) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                      );
                    }
                  }
                },
              ),
              loading: () => const SizedBox.shrink(),
              error: (e, s) => const SizedBox.shrink(),
            ),

            const SizedBox(height: 28),

            // 4. 자유 텍스트 피드백
            promptsAsync.when(
              data: (prompts) => ReviewTextSection(
                initialText: userBook.reviewText,
                prompts: prompts,
                isSaving: detailState.isSaving,
                onSave: (text) async {
                  try {
                    await ref
                        .read(bookDetailProvider(userBookId).notifier)
                        .saveReviewText(text);
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('리뷰가 저장되었습니다')),
                      );
                    }
                  } catch (_) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                      );
                    }
                  }
                },
              ),
              loading: () => const SizedBox.shrink(),
              error: (e, s) => const SizedBox.shrink(),
            ),

            const SizedBox(height: 40),
          ],
        ),
      ),
    );
  }

  void _showStatusBottomSheet(
    BuildContext context,
    WidgetRef ref,
    UserBook userBook,
  ) {
    showModalBottomSheet<BookStatus>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                '읽기 상태 변경',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
              ),
            ),
            ListTile(
              leading: Icon(
                Icons.auto_stories,
                color: userBook.status == BookStatus.reading
                    ? AppColors.primary
                    : null,
              ),
              title: const Text('읽는 중'),
              trailing: userBook.status == BookStatus.reading
                  ? const Icon(Icons.check, color: AppColors.primary)
                  : null,
              onTap: () => Navigator.pop(context, BookStatus.reading),
            ),
            ListTile(
              leading: Icon(
                Icons.check_circle_outline,
                color: userBook.status == BookStatus.read
                    ? AppColors.primary
                    : null,
              ),
              title: const Text('다 읽었어요'),
              trailing: userBook.status == BookStatus.read
                  ? const Icon(Icons.check, color: AppColors.primary)
                  : null,
              onTap: () => Navigator.pop(context, BookStatus.read),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    ).then((status) async {
      if (status == null || status == userBook.status) return;
      try {
        await ref
            .read(bookDetailProvider(userBookId).notifier)
            .updateStatus(status);
      } catch (_) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('상태 변경 실패')),
          );
        }
      }
    });
  }
}

class _BookInfoHeader extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback onStatusChange;

  const _BookInfoHeader({
    required this.userBook,
    required this.onStatusChange,
  });

  @override
  Widget build(BuildContext context) {
    final book = userBook.book;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 표지
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: book?.coverUrl != null
              ? Image.network(
                  book!.coverUrl!,
                  width: 80,
                  height: 120,
                  fit: BoxFit.cover,
                  errorBuilder: (ctx, e, s) => _coverPlaceholder(),
                )
              : _coverPlaceholder(),
        ),
        const SizedBox(width: 16),

        // 정보
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                book?.title ?? '',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary,
                    ),
              ),
              if (book?.author != null) ...[
                const SizedBox(height: 4),
                Text(
                  displayAuthor(book!.author),
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: AppColors.textSecondary,
                      ),
                ),
              ],
              const SizedBox(height: 12),

              // 읽기 상태
              InkWell(
                onTap: onStatusChange,
                borderRadius: BorderRadius.circular(8),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppColors.primary.withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        userBook.status == BookStatus.reading
                            ? Icons.auto_stories
                            : Icons.check_circle_outline,
                        size: 16,
                        color: AppColors.primary,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        userBook.status == BookStatus.reading
                            ? '읽는 중'
                            : '다 읽었어요',
                        style: TextStyle(
                          fontSize: 13,
                          color: AppColors.primary,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(width: 4),
                      Icon(
                        Icons.chevron_right,
                        size: 16,
                        color: AppColors.primary,
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _coverPlaceholder() {
    return Container(
      width: 80,
      height: 120,
      decoration: BoxDecoration(
        color: AppColors.shelf,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Icon(Icons.menu_book, color: AppColors.textSecondary),
    );
  }
}
