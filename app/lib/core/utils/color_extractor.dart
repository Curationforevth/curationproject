import 'dart:ui';
import 'package:flutter/painting.dart';
import 'package:palette_generator/palette_generator.dart';

/// 표지 이미지에서 dominant color 2~3개를 추출
class ColorExtractor {
  /// 이미지 URL에서 dominant colors를 hex 문자열 리스트로 반환
  static Future<List<String>> extractFromUrl(String imageUrl) async {
    try {
      final paletteGenerator = await PaletteGenerator.fromImageProvider(
        NetworkImage(imageUrl),
        maximumColorCount: 4,
        timeout: const Duration(seconds: 10),
      );

      final colors = <Color>[];

      // dominant color 우선
      if (paletteGenerator.dominantColor != null) {
        colors.add(paletteGenerator.dominantColor!.color);
      }

      // 나머지 palette에서 추가 (dominant과 중복 제거)
      for (final paletteColor in paletteGenerator.paletteColors) {
        if (colors.length >= 3) break;
        final c = paletteColor.color;
        if (!colors.any((existing) => _isSimilar(existing, c))) {
          colors.add(c);
        }
      }

      if (colors.isEmpty) return [];
      return colors.map(colorToHex).toList();
    } catch (_) {
      return [];
    }
  }

  /// 두 색상이 유사한지 판단 (너무 비슷한 색 중복 방지)
  static bool _isSimilar(Color a, Color b) {
    final dr = (a.r - b.r).abs();
    final dg = (a.g - b.g).abs();
    final db = (a.b - b.b).abs();
    return (dr + dg + db) < 0.15; // 0~1 범위 기준
  }

  /// Color → hex 문자열 (Color.r/g/b는 0.0~1.0 float)
  static String colorToHex(Color color) {
    final r = (color.r * 255).round().toRadixString(16).padLeft(2, '0');
    final g = (color.g * 255).round().toRadixString(16).padLeft(2, '0');
    final b = (color.b * 255).round().toRadixString(16).padLeft(2, '0');
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
