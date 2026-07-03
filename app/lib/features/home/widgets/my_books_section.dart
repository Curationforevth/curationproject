import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../providers/home_provider.dart';
import 'book_detail_bottom_sheet.dart';
import '../../../core/utils/author_format.dart';

/// 내 책 섹션 — 읽는 중 + 피드백 미작성 책 (최대 5권)
class MyBooksSection extends ConsumerWidget {
  const MyBooksSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final myBooks = ref.watch(myBooksProvider);

    if (myBooks.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(top: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 섹션 타이틀
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Text(
              '내 책',
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: AppColors.textPrimary,
                letterSpacing: 0.01,
              ),
            ),
          ),
          const SizedBox(height: 12),
          // 가로 스크롤 카드 행
          SizedBox(
            height: 268,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 20),
              itemCount: myBooks.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final item = myBooks[index];
                return _MyBookCard(
                  userBook: item.userBook,
                  ctaType: item.ctaType,
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _MyBookCard extends ConsumerWidget {
  final UserBook userBook;
  final String ctaType;

  const _MyBookCard({required this.userBook, required this.ctaType});

  bool get _isReading => ctaType == 'reading';

  Future<void> _onCtaTap(BuildContext context, WidgetRef ref) async {
    if (_isReading) {
      // "다 읽었어요" — 정본 경로(addBookToShelf→resolveShelfWrite)로 전이.
      // raw update 는 recompute 트리거를 우회해, 피드백을 스킵하면 완독
      // 신호가 추천 재계산에 반영되지 않았다(QA 결함).
      try {
        final userBookId =
            await addBookToShelf(ref, userBook.book!, BookStatus.read);
        if (context.mounted) {
          context.push('/feedback/$userBookId');
        }
      } catch (e) {
        if (context.mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('오류가 발생했어요: $e')));
        }
      }
    } else {
      // "피드백 남기기"
      context.push('/feedback/${userBook.id}');
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final book = userBook.book!;

    return GestureDetector(
      // 카드 몸통 탭 = 상세 시트 (인터랙션 통일 규칙 1). CTA 버튼은 내부
      // GestureDetector 가 먼저 잡으므로 그대로 직행(규칙 2 — 1탭 피드백 저니).
      onTap: () => BookDetailBottomSheet.show(context, book),
      child: Container(
        width: 140,
        decoration: BoxDecoration(
          color: const Color(0xFFFAFAFA),
          borderRadius: BorderRadius.circular(14),
        ),
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 표지 이미지
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: SizedBox(
                width: 116,
                height: 166,
                child: book.coverUrl != null
                    ? Image.network(
                        book.coverUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _CoverPlaceholder(),
                      )
                    : _CoverPlaceholder(),
              ),
            ),
            const SizedBox(height: 8),
            // 제목
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 2),
            // 저자
            if (book.author != null && book.author!.isNotEmpty)
              Text(
                displayAuthor(book.author),
                style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w300,
                  color: AppColors.textSecondary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            const Spacer(),
            // CTA 버튼
            SizedBox(
              width: double.infinity,
              child: GestureDetector(
                onTap: () => _onCtaTap(context, ref),
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  decoration: BoxDecoration(
                    color: _isReading
                        ? AppColors.primary
                        : const Color(0xFFF1F5F9),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    _isReading ? '다 읽었어요' : '피드백 남기기',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: _isReading
                          ? AppColors.textOnPrimary
                          : AppColors.textPrimary,
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CoverPlaceholder extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.shelf,
      child: Icon(Icons.menu_book, color: AppColors.textSecondary, size: 32),
    );
  }
}
