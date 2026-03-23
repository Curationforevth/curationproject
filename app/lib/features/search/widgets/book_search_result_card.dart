import 'package:flutter/material.dart';
import '../../../core/models/book.dart';

class BookSearchResultCard extends StatelessWidget {
  final Book book;
  final VoidCallback? onTap;
  final bool isAdded;

  const BookSearchResultCard({
    super.key,
    required this.book,
    this.onTap,
    this.isAdded = false,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: isAdded ? null : onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 표지 이미지
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: book.coverUrl != null && book.coverUrl!.isNotEmpty
                  ? Image.network(
                      book.coverUrl!,
                      width: 60,
                      height: 88,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) => _placeholderCover(),
                    )
                  : _placeholderCover(),
            ),
            const SizedBox(width: 16),

            // 제목 + 저자 + 설명
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    book.title,
                    style: Theme.of(context).textTheme.titleSmall,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (book.author != null && book.author!.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      book.author!,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Colors.grey[600],
                          ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                  if (book.description != null &&
                      book.description!.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(
                      book.description!,
                      style: Theme.of(context).textTheme.bodySmall,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ],
              ),
            ),
            if (isAdded)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.grey[200],
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  '추가됨',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: Colors.grey[600],
                      ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _placeholderCover() {
    return Container(
      width: 60,
      height: 88,
      color: Colors.grey[200],
      child: const Icon(Icons.book, color: Colors.grey),
    );
  }
}
