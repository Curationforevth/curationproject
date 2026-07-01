import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/features/onboarding/services/onboarding_service.dart';

Book _b(String id, String title, {String? cover = 'http://x/c.jpg'}) =>
    Book(id: id, title: title, coverUrl: cover);

void main() {
  group('dedupAndFilterPool', () {
    test('표지 없는 책(null/빈문자열)은 제외', () {
      final out = dedupAndFilterPool([
        _b('1', 'A'),
        _b('2', 'B', cover: null),
        _b('3', 'C', cover: ''),
      ]);
      expect(out.map((b) => b.id), ['1']);
    });

    test('제목 기준 중복 제거 — 첫 항목 유지, 순서 보존', () {
      final out = dedupAndFilterPool([
        _b('1', '동물농장'),
        _b('2', '1984'),
        _b('3', '동물농장'), // 중복 제목
      ]);
      expect(out.map((b) => b.id), ['1', '2']);
    });

    test('제목 앞뒤 공백은 같은 제목으로 취급', () {
      final out = dedupAndFilterPool([
        _b('1', '데미안'),
        _b('2', '  데미안 '),
      ]);
      expect(out.map((b) => b.id), ['1']);
    });

    test('빈 제목은 제외', () {
      final out = dedupAndFilterPool([
        _b('1', '   '),
        _b('2', '유효한 제목'),
      ]);
      expect(out.map((b) => b.id), ['2']);
    });

    test('빈 입력 → 빈 출력', () {
      expect(dedupAndFilterPool([]), isEmpty);
    });
  });
}
