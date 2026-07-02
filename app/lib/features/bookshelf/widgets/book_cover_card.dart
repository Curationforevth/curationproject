import 'package:flutter/material.dart';
import '../../../core/models/book.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/utils/author_format.dart';

/// 표지 이미지 기반 책 카드 (커버 피드용)
class BookCoverCard extends StatelessWidget {
  final Book book;
  final VoidCallback? onTap;

  const BookCoverCard({
    super.key,
    required this.book,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 100,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 표지
            Container(
              width: 100,
              height: 144,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(3),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.06),
                    blurRadius: 4,
                    offset: const Offset(0, 1),
                  ),
                ],
              ),
              clipBehavior: Clip.antiAlias,
              child: book.coverUrl != null
                  ? Image.network(
                      book.coverUrl!,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => _placeholder(),
                    )
                  : _placeholder(),
            ),
            const SizedBox(height: 7),
            // 제목
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: AppColors.textPrimary,
                height: 1.3,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            // 저자
            if (book.author != null && book.author!.isNotEmpty)
              Text(
                displayAuthor(book.author),
                style: TextStyle(
                  fontSize: 11,
                  color: AppColors.textSecondary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
          ],
        ),
      ),
    );
  }

  Widget _placeholder() {
    return Container(
      color: AppColors.shelf,
      child: Icon(Icons.menu_book, color: AppColors.textSecondary, size: 28),
    );
  }
}
