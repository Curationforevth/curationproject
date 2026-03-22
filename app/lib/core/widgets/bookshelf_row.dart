import 'package:flutter/material.dart';
import '../models/book.dart';
import '../theme/app_colors.dart';
import 'book_spine.dart';

class BookshelfRow extends StatelessWidget {
  final List<Book> books;
  final void Function(Book book)? onBookTap;

  const BookshelfRow({
    super.key,
    required this.books,
    this.onBookTap,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // 책등 영역
        SizedBox(
          height: 168, // spine 160 + padding
          child: books.isEmpty
              ? _emptyRow(context)
              : SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: books
                        .map((book) => Padding(
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 2),
                              child: BookSpine(
                                book: book,
                                onTap: onBookTap != null
                                    ? () => onBookTap!(book)
                                    : null,
                              ),
                            ))
                        .toList(),
                  ),
                ),
        ),
        // 선반 (나무판)
        Container(
          height: 10,
          margin: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: AppColors.shelf,
            borderRadius: const BorderRadius.vertical(
              bottom: Radius.circular(4),
            ),
            boxShadow: [
              BoxShadow(
                color: AppColors.shelfDark.withValues(alpha: 0.5),
                blurRadius: 4,
                offset: const Offset(0, 2),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _emptyRow(BuildContext context) {
    return Center(
      child: Container(
        width: 80,
        height: 160,
        decoration: BoxDecoration(
          border: Border.all(
            color: AppColors.textSecondary.withValues(alpha: 0.3),
            style: BorderStyle.solid,
          ),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Icon(
          Icons.add,
          color: AppColors.textSecondary.withValues(alpha: 0.4),
          size: 24,
        ),
      ),
    );
  }
}
