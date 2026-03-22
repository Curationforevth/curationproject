import 'package:flutter/material.dart';
import '../models/book.dart';
import '../theme/app_colors.dart';

/// 컬러 블록 방식 책등 위젯
/// 표지 dominant color 2~3개를 영역 분할 (60/30/10)로 배치
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

  /// 페이지 수 기반 책등 너비 (28~50px)
  double get _width {
    final pages = book.pageCount ?? 250;
    return (pages / 8).clamp(28, 50).toDouble();
  }

  /// dominant colors → 3개 컬러 블록 (없으면 해시 기반 폴백)
  List<Color> get _colors {
    if (book.dominantColors != null && book.dominantColors!.isNotEmpty) {
      final parsed = book.dominantColors!
          .map((hex) => _parseHex(hex))
          .whereType<Color>()
          .toList();
      if (parsed.length >= 2) {
        // 2~3개 색상을 3개 블록으로 (부족하면 첫 색 반복)
        return [
          parsed[0],
          parsed.length > 1 ? parsed[1] : parsed[0],
          parsed.length > 2 ? parsed[2] : parsed[0],
        ];
      }
    }
    // 폴백: 해시 기반 색상
    final base = AppColors.spineColorFromTitle(book.title);
    final hsl = HSLColor.fromColor(base);
    return [
      base,
      hsl.withLightness((hsl.lightness + 0.15).clamp(0.0, 1.0)).toColor(),
      hsl.withLightness((hsl.lightness + 0.3).clamp(0.0, 1.0)).toColor(),
    ];
  }

  /// 배경색 대비 텍스트 색상
  Color _textColorOn(Color bg) {
    return bg.computeLuminance() > 0.4 ? Colors.black87 : Colors.white;
  }

  /// 폰트 패밀리 (spine_font 필드 또는 기본값)
  String get _fontFamily => book.spineFont ?? 'Pretendard';

  /// hex 문자열 → Color
  static Color? _parseHex(String hex) {
    try {
      final clean = hex.replaceAll('#', '');
      if (clean.length == 6) {
        return Color(int.parse('FF$clean', radix: 16));
      }
    } catch (_) {}
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final colors = _colors;
    final primaryColor = colors[0];
    final secondaryColor = colors[1];
    final accentColor = colors[2];
    final titleColor = _textColorOn(primaryColor);
    final authorColor = _textColorOn(secondaryColor).withValues(alpha: 0.6);

    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: _width,
        height: height,
        clipBehavior: Clip.antiAlias,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(3),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.1),
              blurRadius: 3,
              offset: const Offset(1, 1),
            ),
          ],
        ),
        child: Column(
          children: [
            // 주색 블록 (60%) — 제목
            Expanded(
              flex: 6,
              child: Container(
                color: primaryColor,
                padding: const EdgeInsets.only(top: 10, left: 5, right: 4),
                child: RotatedBox(
                  quarterTurns: 1,
                  child: Text(
                    book.title,
                    style: TextStyle(
                      color: titleColor,
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      fontFamily: _fontFamily,
                      letterSpacing: 0.5,
                    ),
                  ),
                ),
              ),
            ),
            // 보조색 블록 (30%) — 저자
            Expanded(
              flex: 3,
              child: Container(
                color: secondaryColor,
                padding: const EdgeInsets.only(bottom: 6, left: 5, right: 4),
                alignment: Alignment.bottomLeft,
                child: book.author != null && book.author!.isNotEmpty
                    ? RotatedBox(
                        quarterTurns: 1,
                        child: Text(
                          book.author!,
                          style: TextStyle(
                            color: authorColor,
                            fontSize: 7,
                            fontFamily: 'Pretendard',
                          ),
                        ),
                      )
                    : null,
              ),
            ),
            // 액센트 띠 (10%)
            Expanded(
              flex: 1,
              child: Container(color: accentColor),
            ),
          ],
        ),
      ),
    );
  }
}
