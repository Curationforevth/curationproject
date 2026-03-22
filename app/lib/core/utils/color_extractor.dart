import 'dart:ui';

/// 표지 이미지에서 dominant color 2~3개를 추출
///
/// palette_generator 패키지를 사용하여 구현 예정.
/// 현재는 placeholder — 실제 이미지 분석은 패키지 연동 후 구현.
class ColorExtractor {
  /// 이미지 URL에서 dominant colors를 hex 문자열 리스트로 반환
  ///
  /// TODO: palette_generator 패키지 연동
  /// ```dart
  /// final paletteGenerator = await PaletteGenerator.fromImageProvider(
  ///   NetworkImage(imageUrl),
  ///   maximumColorCount: 3,
  /// );
  /// ```
  static Future<List<String>> extractFromUrl(String imageUrl) async {
    // placeholder: 실제 구현 전까지 빈 리스트 반환
    // 빈 리스트면 BookSpine이 해시 기반 폴백 사용
    return [];
  }

  /// Color → hex 문자열
  static String colorToHex(Color color) {
    final r = color.r.toInt().toRadixString(16).padLeft(2, '0');
    final g = color.g.toInt().toRadixString(16).padLeft(2, '0');
    final b = color.b.toInt().toRadixString(16).padLeft(2, '0');
    return '#${r.toUpperCase()}${g.toUpperCase()}${b.toUpperCase()}';
  }

  /// hex 문자열 → Color
  static Color? hexToColor(String hex) {
    try {
      final clean = hex.replaceAll('#', '');
      if (clean.length == 6) {
        return Color(int.parse('FF$clean', radix: 16));
      }
    } catch (_) {}
    return null;
  }
}
