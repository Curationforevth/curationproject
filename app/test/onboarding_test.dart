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

  group('buildOnboardingRows', () {
    // Eden 결정 2026-07-02: 온보딩 그리드는 "읽었다" 신호일 뿐 "좋았다"가 아님 —
    // 최애만 rating='good', 나머지는 null 로 남겨 홈에서 진짜 평가를 유도한다.
    test('최애 책만 rating=good, 나머지는 rating=null', () {
      final rows = buildOnboardingRows(
        userId: 'u1',
        selectedBookIds: ['b1', 'b2', 'b3'],
        favoriteBookId: 'b2',
      );

      expect(rows, hasLength(3));
      final byId = {for (final r in rows) r['book_id'] as String: r};
      expect(byId['b1']!['rating'], isNull);
      expect(byId['b2']!['rating'], 'good');
      expect(byId['b3']!['rating'], isNull);
    });

    test('모든 행의 status 는 finished (wishlist CHECK 회피)', () {
      final rows = buildOnboardingRows(
        userId: 'u1',
        selectedBookIds: ['b1', 'b2'],
        favoriteBookId: 'b1',
      );
      expect(rows.every((r) => r['status'] == 'finished'), isTrue);
    });

    test('모든 행이 동일한 키 집합을 가진다 (PostgREST 배치 insert PGRST102 회피)', () {
      final rows = buildOnboardingRows(
        userId: 'u1',
        selectedBookIds: ['b1', 'b2'],
        favoriteBookId: 'b1',
        favoriteEmotionTags: ['잔잔한'],
      );
      const expectedKeys = {
        'user_id',
        'book_id',
        'status',
        'rating',
        'emotion_tags'
      };
      for (final r in rows) {
        expect(r.keys.toSet(), expectedKeys);
      }
    });

    test('감성태그는 최애 책에만 부여, 나머지는 null', () {
      final rows = buildOnboardingRows(
        userId: 'u1',
        selectedBookIds: ['b1', 'b2'],
        favoriteBookId: 'b1',
        favoriteEmotionTags: ['잔잔한', '몰입'],
      );
      final byId = {for (final r in rows) r['book_id'] as String: r};
      expect(byId['b1']!['emotion_tags'], ['잔잔한', '몰입']);
      expect(byId['b2']!['emotion_tags'], isNull);
    });

    test('favoriteBookId 가 null 이면 전부 rating=null', () {
      final rows = buildOnboardingRows(
        userId: 'u1',
        selectedBookIds: ['b1', 'b2'],
        favoriteBookId: null,
      );
      expect(rows.every((r) => r['rating'] == null), isTrue);
    });

    test('user_id/book_id 가 각 행에 정확히 매핑된다', () {
      final rows = buildOnboardingRows(
        userId: 'u42',
        selectedBookIds: ['b1'],
        favoriteBookId: 'b1',
      );
      expect(rows.single['user_id'], 'u42');
      expect(rows.single['book_id'], 'b1');
    });
  });
}
