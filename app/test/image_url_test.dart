import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/utils/image_url.dart';

void main() {
  group('highResCoverUrl', () {
    test('카카오 썸네일 → fname 원본', () {
      expect(
        highResCoverUrl(
            'https://search1.kakaocdn.net/thumb/R120x174.q85/?fname=https%3A%2F%2Ft1.daumcdn.net%2Flbook%2Fimage%2F123'),
        'https://t1.daumcdn.net/lbook/image/123',
      );
    });

    test('알라딘 구형 /cover/(접미사 없음) → cover500 — 이기적 유전자 실기기 케이스', () {
      expect(
        highResCoverUrl(
            'https://image.aladin.co.kr/product/17048/25/cover/8932473900_1.jpg'),
        'https://image.aladin.co.kr/product/17048/25/cover500/8932473900_1.jpg',
      );
      // http 스킴(구 데이터)도 동일 처리
      expect(
        highResCoverUrl(
            'http://image.aladin.co.kr/product/751/8/cover/8932471630_1.jpg'),
        'http://image.aladin.co.kr/product/751/8/cover500/8932471630_1.jpg',
      );
    });

    test('알라딘 coversum/cover200/cover150 → cover500', () {
      expect(
        highResCoverUrl(
            'https://image.aladin.co.kr/product/38694/70/coversum/k612136721_2.jpg'),
        'https://image.aladin.co.kr/product/38694/70/cover500/k612136721_2.jpg',
      );
      expect(
        highResCoverUrl(
            'https://image.aladin.co.kr/product/37971/81/cover200/k612034864_1.jpg'),
        'https://image.aladin.co.kr/product/37971/81/cover500/k612034864_1.jpg',
      );
      expect(
        highResCoverUrl(
            'https://image.aladin.co.kr/product/1/2/cover150/x.jpg'),
        'https://image.aladin.co.kr/product/1/2/cover500/x.jpg',
      );
    });

    test('idempotent — cover500/기타 URL 은 그대로', () {
      const already =
          'https://image.aladin.co.kr/product/38694/70/cover500/k612136721_2.jpg';
      expect(highResCoverUrl(already), already);
      expect(highResCoverUrl(highResCoverUrl(already)), already);
      const other = 'https://example.com/covers/abc.jpg';
      expect(highResCoverUrl(other), other);
      expect(highResCoverUrl(null), null);
      expect(highResCoverUrl(''), '');
    });
  });
}
