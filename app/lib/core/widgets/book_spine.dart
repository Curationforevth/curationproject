import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../models/book.dart';
import '../theme/app_colors.dart';

/// 컬러 블록 방식 책등 위젯.
///
/// 텍스트 규칙(레퍼런스 기반 — 한글 세로쓰기 + 책등 조판):
/// - 부제 제거: 책등엔 본제목·대표저자만 (부제는 표지에만).
/// - 균일 폰트: 모든 책 같은 크기(책마다 글자 크기 다르게 하지 않음, scaleDown 금지).
/// - 한글 정방향 세로쓰기(위→아래). 라틴/숫자는 회전 없이 정자.
/// - 단일 컬럼 우선, 넘치면 좌종서(왼→오른)로 다음 컬럼 — 잘림·축소 없이 임의 길이 처리.
///   2컬럼 이상이면 텍스트가 가장자리에 붙지 않게 책등 폭을 소량만 넓혀 좌우 여백 확보.
/// 전체 제목은 탭 → 책 상세에서 확인.
class BookSpine extends StatelessWidget {
  final Book book;
  final double height;
  final VoidCallback? onTap;

  const BookSpine({
    super.key,
    required this.book,
    this.height = 190,
    this.onTap,
  });

  // 균일 타이포 (레퍼런스: 일관성 · 좁은 폭에서도 읽히는 볼드).
  static const double _titleFs = 11.0;
  static const double _authorFs = 8.0;
  static const double _lineH = 1.06;

  // 블록 세로 비율 (제목 66% · 저자 28% · 액센트 6%).
  static const int _titleFlex = 66;
  static const int _authorFlex = 28;
  static const int _accentFlex = 6;

  /// 페이지 수 기반 책등 너비 (30~50px). 2컬럼 이상이면 좌우 숨쉴 여백을 위해 소량 가산.
  double get _width {
    final pages = book.pageCount ?? 250;
    final base = (pages / 8).clamp(30, 50).toDouble();
    final cols = _spineColumns;
    if (cols < 2) return base;
    // 균일 폰트·잘림 없음 유지 — 폭으로만 여백 확보. 티 안 나게 컬럼당 소량, 상한 8px.
    final extra = ((cols - 1) * 3.0).clamp(0.0, 8.0);
    return base + extra;
  }

  /// 세로 조판 시 필요한 컬럼 수(제목·저자 중 큰 값) — 폭 결정용.
  /// `_verticalText` 의 word-aware 패킹과 동일 로직이어야 함(둘을 동기화 유지).
  int get _spineColumns {
    final titleCols =
        _columnsFor(_spineTitle, _titleFs, height * _titleFlex / 100 - 14);
    final a = _spineAuthor;
    final authorCols =
        a == null ? 1 : _columnsFor(a, _authorFs, height * _authorFlex / 100 - 7);
    return titleCols > authorCols ? titleCols : authorCols;
  }

  /// 주어진 텍스트를 세로 컬럼으로 쌓을 때 필요한 컬럼 수(`_verticalText` 미러).
  int _columnsFor(String text, double fs, double availH) {
    if (availH <= 0) return 1;
    final lineH = fs * _lineH;
    final spaceGap = fs * 0.45;
    var cols = 1;
    var h = 0.0;
    var placed = false;
    for (final word in text.split(RegExp(r'\s+')).where((w) => w.isNotEmpty)) {
      final chars = word.characters.length;
      if (placed && h + spaceGap + chars * lineH > availH) {
        cols++;
        h = 0;
        placed = false;
      } else if (placed) {
        h += spaceGap;
      }
      for (var i = 0; i < chars; i++) {
        if (placed && h + lineH > availH) {
          cols++;
          h = 0;
          placed = false;
        }
        h += lineH;
        placed = true;
      }
    }
    return cols;
  }

  /// 책등 표시용 본제목 — 부제/병기/시리즈 번호 제거.
  String get _spineTitle {
    var s = book.title.split(RegExp(r'[:：]')).first; // 부제 제거
    s = s.replaceAll(RegExp(r'\([^)]*\)'), ''); // (It) 같은 병기 제거
    s = s.replaceAll(RegExp(r'\s+\d+\s*$'), ''); // 끝 시리즈 번호
    s = s.trim();
    if (RegExp(r'[가-힣]').hasMatch(s)) {
      // 한글+라틴 병기면 라틴 꼬리 제거 (예: "수확자 Scythe" → "수확자")
      s = s.replaceAll(RegExp(r'\s+[A-Za-z][A-Za-z\s]*$'), '').trim();
    }
    return s.isEmpty ? book.title : s;
  }

  /// 책등 표시용 대표저자 — 첫 저자, 역자/역할 표기 제거.
  String? get _spineAuthor {
    final a = book.author;
    if (a == null || a.isEmpty) return null;
    var s = a.split(RegExp(r'[,·]')).first;
    s = s.replaceAll(RegExp(r'\((지은이|옮긴이|글|그림|저|편|글·그림)\)'), '');
    s = s.replaceAll(RegExp(r'\s*(지음|옮김|글|그림)\s*$'), '');
    s = s.trim();
    return s.isEmpty ? a : s;
  }

  /// dominant colors → 3개 컬러 블록 (없으면 해시 기반 폴백).
  List<Color> get _colors {
    if (book.dominantColors != null && book.dominantColors!.isNotEmpty) {
      final parsed = book.dominantColors!
          .map((hex) => _parseHex(hex))
          .whereType<Color>()
          .toList();
      if (parsed.length >= 2) {
        return [
          parsed[0],
          parsed.length > 1 ? parsed[1] : parsed[0],
          parsed.length > 2 ? parsed[2] : parsed[0],
        ];
      }
    }
    final base = AppColors.spineColorFromTitle(book.title);
    final hsl = HSLColor.fromColor(base);
    return [
      base,
      hsl.withLightness((hsl.lightness + 0.15).clamp(0.0, 1.0)).toColor(),
      hsl.withLightness((hsl.lightness + 0.3).clamp(0.0, 1.0)).toColor(),
    ];
  }

  Color _textColorOn(Color bg) {
    return bg.computeLuminance() > 0.4 ? Colors.black87 : Colors.white;
  }

  /// 제목 TextStyle — spineFont(장르별 폰트) 유지, 크기는 균일. 실패 시 기본 폰트 폴백.
  TextStyle _titleStyle(Color color) {
    final fontName = book.spineFont ?? 'Pretendard';
    try {
      return GoogleFonts.getFont(
        fontName,
        color: color,
        fontSize: _titleFs,
        fontWeight: FontWeight.w700,
        height: _lineH,
      );
    } catch (_) {
      return TextStyle(
        color: color,
        fontSize: _titleFs,
        fontWeight: FontWeight.w700,
        height: _lineH,
      );
    }
  }

  static Color? _parseHex(String hex) {
    try {
      final clean = hex.replaceAll('#', '');
      if (clean.length == 6) {
        return Color(int.parse('FF$clean', radix: 16));
      }
    } catch (_) {}
    return null;
  }

  /// 한글 정방향 세로쓰기 렌더 — 제목·저자에 동일하게 쓰는 하나의 룰.
  /// 글자는 위→아래로 쌓고, 공백은 작은 간격, 블록 높이를 넘으면 다음 컬럼.
  /// 컬럼 진행은 좌종서(왼→오른) — 전통 산문은 우종서지만, 현대의 짧은 간판·표지판성
  /// 2줄 세로쓰기는 가로쓰기(좌→우) 습관을 따라 좌종서가 관례(나무위키 세로쓰기/방향).
  /// 폰트 축소·잘림 없이 임의 길이를 처리(폭=책두께가 컬럼 수 상한).
  Widget _verticalText(String text, TextStyle style) {
    final fs = style.fontSize ?? _titleFs;
    final lineH = fs * _lineH;
    final spaceGap = fs * 0.45; // 공백은 한 글자보다 작은 어절 간격
    return LayoutBuilder(
      builder: (context, constraints) {
        final availH = constraints.maxHeight;
        // word-aware 세로 조판: 어절(공백) 경계에서 우선 줄바꿈해 이름·제목이 단어
        // 중간에서 쪼개지지 않게 한다("히가시노 게이고" → 히가시노 | 게이고).
        // 한 어절이 컬럼 높이를 넘으면 그 어절만 글자 단위 폴백으로 쪼갠다.
        final columns = <List<Widget>>[];
        var col = <Widget>[];
        var h = 0.0;
        void flush() {
          if (col.isNotEmpty) {
            columns.add(col);
            col = <Widget>[];
            h = 0;
          }
        }

        Widget glyph(String g) => SizedBox(
              height: lineH,
              child: Center(
                  child: Text(g, textAlign: TextAlign.center, style: style)),
            );

        for (final word in text.split(RegExp(r'\s+')).where((w) => w.isNotEmpty)) {
          final chars = word.characters.toList();
          // 다음 어절이 현재 컬럼에 안 들어가면 새 컬럼(어절 경계 줄바꿈).
          if (col.isNotEmpty && h + spaceGap + chars.length * lineH > availH) {
            flush();
          } else if (col.isNotEmpty) {
            col.add(SizedBox(height: spaceGap)); // 같은 컬럼 내 어절 간격
            h += spaceGap;
          }
          for (final ch in chars) {
            if (col.isNotEmpty && h + lineH > availH) flush(); // 초장 어절 폴백
            col.add(glyph(ch));
            h += lineH;
          }
        }
        flush();

        // 좌종서(왼→오른): 컬럼 자연 순서(첫 컬럼이 맨 왼쪽).
        // ClipRect+OverflowBox: 극단 초장문이 폭을 넘어도 assertion 없이 조용히 clip.
        return ClipRect(
          child: OverflowBox(
            maxWidth: double.infinity,
            maxHeight: double.infinity,
            alignment: Alignment.center,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final c in columns)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 0.5),
                    child: Column(mainAxisSize: MainAxisSize.min, children: c),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final colors = _colors;
    final primaryColor = colors[0];
    final secondaryColor = colors[1];
    final accentColor = colors[2];
    final titleColor = _textColorOn(primaryColor);
    final authorColor = _textColorOn(secondaryColor).withValues(alpha: 0.72);
    final author = _spineAuthor;

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
            // 주색 블록 (66%) — 제목 dominant (세로쓰기, 중앙 정렬)
            Expanded(
              flex: _titleFlex,
              child: Container(
                width: double.infinity,
                color: primaryColor,
                padding: const EdgeInsets.fromLTRB(3, 9, 3, 5),
                alignment: Alignment.center,
                // 세로쓰기는 글자별 렌더라 스크린리더가 낱자로 읽는다 → Semantics 로
                // 전체 제목을 라벨링하고 자식 시맨틱스 제외(접근성 + 텍스트 탐색 가능).
                child: Semantics(
                  label: book.title,
                  excludeSemantics: true,
                  child: _verticalText(_spineTitle, _titleStyle(titleColor)),
                ),
              ),
            ),
            // 보조색 블록 (28%) — 저자 secondary (세로쓰기, 하단)
            Expanded(
              flex: _authorFlex,
              child: Container(
                width: double.infinity,
                color: secondaryColor,
                padding: const EdgeInsets.fromLTRB(3, 2, 3, 5),
                alignment: Alignment.bottomCenter,
                child: author != null
                    ? Semantics(
                        label: book.author,
                        excludeSemantics: true,
                        child: _verticalText(
                          author,
                          TextStyle(
                            color: authorColor,
                            fontSize: _authorFs,
                            fontFamily: 'Pretendard',
                            fontWeight: FontWeight.w500,
                            height: _lineH,
                          ),
                        ),
                      )
                    : null,
              ),
            ),
            // 액센트 띠 (6%)
            Expanded(
              flex: _accentFlex,
              child: Container(width: double.infinity, color: accentColor),
            ),
          ],
        ),
      ),
    );
  }
}
