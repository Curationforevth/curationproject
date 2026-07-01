import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/services/book_registration_service.dart';

void main() {
  // 화차 중복 버그 회귀 방지: 추천/홈/비슷한책에서 담을 때 toBook()은 기존 books.id는
  // 싣지만 isbn·source는 null이다. registerBook 이 book.id 를 무시하고 isbn 만 보면
  // insert 경로로 null-isbn 복제행을 새로 만들어 같은 책이 서재에 두 번 담긴다.
  // resolveBookRef 는 이 식별 전략을 순수 로직으로 고정한다.
  group('resolveBookRef — 등록 시 books 행 식별 전략', () {
    test('내부소스(추천/홈/유사): book.id 있으면 기존 행 재사용 → 복제행 금지', () {
      // toBook(): id 는 실제 카탈로그 uuid, isbn 은 null
      final fromFeed =
          Book(id: 'existing-catalog-uuid', title: '화차', author: '미야베 미유키');
      expect(fromFeed.isbn, isNull);
      expect(resolveBookRef(fromFeed), BookRef.reuseId);
    });

    test('외부검색(카카오): id 비어있고 isbn 있으면 isbn upsert', () {
      final fromSearch = Book(
          id: '', isbn: '9788954617437', title: '화차', author: '미야베 미유키');
      expect(resolveBookRef(fromSearch), BookRef.upsertByIsbn);
    });

    test('id·isbn 둘 다 없으면 insertNew (드문 예외 경로)', () {
      final bare = Book(id: '', title: '제목만 있는 책', author: '저자');
      expect(resolveBookRef(bare), BookRef.insertNew);
    });

    test('id 우선: id·isbn 둘 다 있어도 reuseId (기존 행 신뢰)', () {
      final both = Book(id: 'uuid', isbn: '9788954617437', title: '화차');
      expect(resolveBookRef(both), BookRef.reuseId);
    });
  });
}
