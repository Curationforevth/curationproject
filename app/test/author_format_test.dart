import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/utils/author_format.dart';

void main() {
  // DB normalize_primary_author(마이그레이션 20260702000000)·서버
  // generate_curation_themes 와 동일 규칙 — 케이스도 동일하게 유지(3층 동기).
  group('displayAuthor', () {
    test('알라딘 역할 표기 제거', () {
      expect(displayAuthor('이해 (지은이)'), '이해');
      expect(displayAuthor('애거서 크리스티 (지은이), 공경희 (옮긴이)'), '애거서 크리스티');
    });

    test('꼬리 역할어 제거 (정보나루 표기)', () {
      expect(displayAuthor('요한 하리 지음'), '요한 하리');
    });

    test('깨끗한 저자는 그대로', () {
      expect(displayAuthor('한강'), '한강');
    });

    test('복수 저자는 대표 저자만', () {
      expect(displayAuthor('무적핑크, 핑크잼 (지은이), 와이랩(YLAB) (기획)'), '무적핑크');
    });

    test('null/빈 문자열', () {
      expect(displayAuthor(null), '');
      expect(displayAuthor('  '), '');
    });
  });
}
