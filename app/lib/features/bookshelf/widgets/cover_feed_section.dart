import 'package:flutter/material.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import 'book_cover_card.dart';

/// 커버 피드 섹션 — 제목 + 카운트 + 가로 스크롤 표지 카드 행
class CoverFeedSection extends StatelessWidget {
  final String title;
  final List<UserBook> userBooks;
  final void Function(UserBook) onBookTap;

  const CoverFeedSection({
    super.key,
    required this.title,
    required this.userBooks,
    required this.onBookTap,
  });

  @override
  Widget build(BuildContext context) {
    final booksWithData = userBooks.where((ub) => ub.book != null).toList();
    if (booksWithData.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 섹션 헤더
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: AppColors.textPrimary,
                    letterSpacing: 0.01,
                  ),
                ),
                Text(
                  '${booksWithData.length}권',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          // 가로 스크롤 행
          SizedBox(
            height: 190,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 20),
              itemCount: booksWithData.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final ub = booksWithData[index];
                return BookCoverCard(
                  book: ub.book!,
                  onTap: () => onBookTap(ub),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
