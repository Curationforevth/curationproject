import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
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

  // '읽었어요' 재등록 23505 회귀 방지: user_books 는 (user_id, book_id) UNIQUE.
  // registerBook 이 무조건 INSERT 하면 이미 서재에 있는 책(북마크해둔 책 등)에
  // '읽었어요'를 누를 때 duplicate key 에러 — 화차 fix(id 재사용)가 드러낸 잠복 결함.
  // 기존 행이 있으면 status 전이(UPDATE)로, wishlist 로의 강등은 금지한다
  // (finished+rating 행을 wishlist 로 내리면 user_books_wishlist_no_rating CHECK 위반).
  group('resolveShelfWrite — 서재 등록/전이 전략', () {
    test('기존 행 없으면 insertNew', () {
      expect(
        resolveShelfWrite(existingStatus: null, requested: BookStatus.read),
        ShelfWrite.insertNew,
      );
    });

    test('북마크해둔 책에 읽었어요 → 상태 전이 (에러났던 바로 그 경로)', () {
      expect(
        resolveShelfWrite(existingStatus: 'wishlist', requested: BookStatus.read),
        ShelfWrite.updateStatus,
      );
    });

    test('읽는 중 → 읽었어요 전이', () {
      expect(
        resolveShelfWrite(existingStatus: 'reading', requested: BookStatus.read),
        ShelfWrite.updateStatus,
      );
    });

    test('같은 상태 재탭은 no-op (keepExisting)', () {
      expect(
        resolveShelfWrite(existingStatus: 'finished', requested: BookStatus.read),
        ShelfWrite.keepExisting,
      );
      expect(
        resolveShelfWrite(
            existingStatus: 'wishlist', requested: BookStatus.wantToRead),
        ShelfWrite.keepExisting,
      );
    });

    test('이미 서재에 있는 책의 북마크(wishlist) 요청 = 강등 금지', () {
      // finished 행에 rating 이 있으면 wishlist 전이는 CHECK 위반이기도 하다.
      expect(
        resolveShelfWrite(
            existingStatus: 'finished', requested: BookStatus.wantToRead),
        ShelfWrite.keepExisting,
      );
      expect(
        resolveShelfWrite(
            existingStatus: 'reading', requested: BookStatus.wantToRead),
        ShelfWrite.keepExisting,
      );
    });

    test('wishlist → reading 승격 허용', () {
      expect(
        resolveShelfWrite(
            existingStatus: 'wishlist', requested: BookStatus.reading),
        ShelfWrite.updateStatus,
      );
    });
  });
}
