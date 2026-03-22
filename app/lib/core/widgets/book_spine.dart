import 'package:flutter/material.dart';
import '../models/book.dart';
import '../theme/app_colors.dart';

class BookSpine extends StatelessWidget {
  final Book book;
  final double height;
  final VoidCallback? onTap;

  const BookSpine({
    super.key,
    required this.book,
    this.height = 160,
    this.onTap,
  });

  /// 페이지 수 기반 책등 너비 (30~60px)
  double get _width {
    final pages = book.pageCount ?? 250;
    return (pages / 10).clamp(30, 60).toDouble();
  }

  /// 제목 해시 기반 배경색
  Color get _backgroundColor => AppColors.spineColorFromTitle(book.title);

  /// 배경색 밝기에 따라 텍스트 색상 결정
  Color get _textColor =>
      _backgroundColor.computeLuminance() > 0.4 ? Colors.black87 : Colors.white;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: _width,
        height: height,
        decoration: BoxDecoration(
          color: _backgroundColor,
          borderRadius: BorderRadius.circular(3),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.15),
              blurRadius: 2,
              offset: const Offset(1, 1),
            ),
          ],
        ),
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
        child: Column(
          children: [
            // 제목 (세로)
            Expanded(
              flex: 3,
              child: RotatedBox(
                quarterTurns: 1,
                child: Center(
                  child: Text(
                    book.title,
                    style: TextStyle(
                      color: _textColor,
                      fontSize: 10,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 0.5,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
            ),
            // 저자 (세로)
            if (book.author != null && book.author!.isNotEmpty)
              Expanded(
                flex: 1,
                child: RotatedBox(
                  quarterTurns: 1,
                  child: Center(
                    child: Text(
                      book.author!,
                      style: TextStyle(
                        color: _textColor.withValues(alpha: 0.7),
                        fontSize: 7,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
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
