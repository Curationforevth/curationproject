// 책등(BookSpine) 실렌더 검증용 golden 테스트.
// `flutter test --update-goldens test/book_spine_golden_test.dart` 로 PNG 생성 후 눈으로 가독성 확인.
// 시스템 한글 폰트(AppleGothic)를 'Pretendard' 로 로드해 실제 한글이 렌더되게 한다.
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/widgets/book_spine.dart';

Future<void> _loadKoreanFont() async {
  const path = '/System/Library/Fonts/Supplemental/AppleGothic.ttf';
  final bytes = await File(path).readAsBytes();
  final data = ByteData.view(Uint8List.fromList(bytes).buffer);
  // BookSpine 폴백/저자가 쓰는 family 'Pretendard' 로 로드.
  final l1 = FontLoader('Pretendard')..addFont(Future.value(data));
  await l1.load();
}

Book _b(String title, String author, List<String> colors, int pages) => Book(
      id: title,
      title: title,
      author: author,
      dominantColors: colors,
      pageCount: pages,
    );

void main() {
  setUpAll(() async {
    GoogleFonts.config.allowRuntimeFetching = false;
    await _loadKoreanFont();
  });

  testWidgets('book spine shelf renders readable Korean vertical', (tester) async {
    final books = <Book>[
      _b('화차', '미야베 미유키', ['3a4a63', '5a6b85', '2f3a4a'], 320),
      _b('쇼코의 미소', '최은영', ['7a3b3b', '9a5a5a', '5a2a2a'], 264),
      _b('그것(It) 1', '스티븐 킹', ['2f4a3a', '4a6b52', '20301f'], 400),
      _b('수확자 Scythe', '닐 셔스터먼', ['5a4a2f', '7a6a45', '3a2f1f'], 300),
      _b('신과 함께 :저승편', '주호민', ['4a3a5a', '6a5a7a', '2f2540'], 250),
      _b('단 한 번의 삶', '김영하', ['2f3a4a', '4a5a6a', '1f2a35'], 230),
      _b('나미야 잡화점의 기적', '히가시노 게이고 (지은이), 양윤옥 (옮긴이)',
          ['6a3a4a', '8a5a6a', '4a2535'], 280),
      _b('안녕이라 그랬어 :김애란 소설', '김애란', ['3a5a5a', '5a7a7a', '2a4040'], 200),
      _b('뒤틀린 집', '전건우', ['3a4a63', '5a6b85', '2f3a4a'], 340),
      _b('우리가 빛의 속도로 갈 수 없다면 :김초엽 소설', '김초엽',
          ['7a3b3b', '9a5a5a', '5a2a2a'], 330),
      _b('긴키 지방의 어느 장소에 대하여 :세스지 장편소설', '세스지',
          ['2f4a3a', '4a6b52', '20301f'], 240),
      _b('도둑맞은 집중력 :집중력 위기의 시대, 삶의 주도권을 되찾는 법', '요한 하리 지음',
          ['5a4a2f', '7a6a45', '3a2f1f'], 360),
      // 룰 스트레스 테스트: 가상 초장문(37자)
      _b('미움받을 용기 자기혐오에서 벗어나 진정한 나를 찾는 아들러 심리학의 가르침', '기시미 이치로',
          ['4a3a5a', '6a5a7a', '2f2540'], 300),
    ];

    await tester.pumpWidget(
      MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          backgroundColor: const Color(0xFF0d1117),
          body: DefaultTextStyle(
            style: const TextStyle(fontFamily: 'Pretendard'),
            child: Center(
              child: RepaintBoundary(
                child: Container(
                  color: const Color(0xFF12161c),
                  padding: const EdgeInsets.fromLTRB(20, 26, 20, 10),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      for (final b in books)
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 4),
                          child: BookSpine(book: b),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await expectLater(
      find.byType(RepaintBoundary).first,
      matchesGoldenFile('goldens/spine_shelf.png'),
    );
  });
}
