import 'package:flutter/material.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';

/// 피드백 유도 CTA — 미니 표지 겹침 + "N권의 피드백이 비어있어요"
class FeedbackCtaRow extends StatelessWidget {
  final List<UserBook> unreviewedBooks;
  final VoidCallback? onTap;

  const FeedbackCtaRow({
    super.key,
    required this.unreviewedBooks,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    if (unreviewedBooks.isEmpty) return const SizedBox.shrink();

    final displayBooks = unreviewedBooks.take(3).toList();

    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 20),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: AppColors.accent.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: [
            // 미니 표지 겹침
            SizedBox(
              width: 36.0 + (displayBooks.length - 1) * 28.0,
              height: 52,
              child: Stack(
                children: List.generate(displayBooks.length, (i) {
                  final book = displayBooks[i].book;
                  return Positioned(
                    left: i * 28.0,
                    child: Container(
                      width: 36,
                      height: 52,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(2),
                        border: Border.all(color: Colors.white, width: 2),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.08),
                            blurRadius: 3,
                            offset: const Offset(0, 1),
                          ),
                        ],
                      ),
                      clipBehavior: Clip.antiAlias,
                      child: book?.coverUrl != null
                          ? Image.network(
                              book!.coverUrl!,
                              fit: BoxFit.cover,
                              errorBuilder: (_, __, ___) => Container(
                                color: AppColors.shelf,
                              ),
                            )
                          : Container(color: AppColors.shelf),
                    ),
                  );
                }),
              ),
            ),
            const SizedBox(width: 12),
            // 텍스트
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${unreviewedBooks.length}권의 피드백이 비어있어요',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: AppColors.accent,
                      height: 1.4,
                    ),
                  ),
                  Text(
                    '기록하면 취향을 분석해드려요',
                    style: TextStyle(
                      fontSize: 11,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            // 화살표
            Icon(
              Icons.chevron_right,
              size: 20,
              color: AppColors.accent,
            ),
          ],
        ),
      ),
    );
  }
}
