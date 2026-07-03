import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/home/widgets/book_detail_bottom_sheet.dart';

UserBook _ub(BookStatus s, {String? rating}) => UserBook(
    id: 'ub1', userId: 'u1', bookId: 'b1', status: s, rating: rating);

SheetActionSpec _spec(UserBook? ub, Map<String, int> hits) => actionsForState(
      userBook: ub,
      onReading: () => hits['reading'] = (hits['reading'] ?? 0) + 1,
      onRead: () => hits['read'] = (hits['read'] ?? 0) + 1,
      onBookmark: () => hits['bookmark'] = (hits['bookmark'] ?? 0) + 1,
      onNotInterested: () => hits['ni'] = (hits['ni'] ?? 0) + 1,
      onDelete: () => hits['delete'] = (hits['delete'] ?? 0) + 1,
      onRevert: () => hits['revert'] = (hits['revert'] ?? 0) + 1,
      onOpenFeedback: () => hits['feedback'] = (hits['feedback'] ?? 0) + 1,
    );

void main() {
  test('미보유 — 프라이머리 읽었어요 + 로우 3개(읽는중·읽고싶어요·관심없어요[danger])', () {
    final h = <String, int>{};
    final s = _spec(null, h);
    expect(s.primaryLabel, '읽었어요');
    expect(s.showRatingCard, isFalse);
    expect(s.row.map((a) => a.label).toList(),
        ['읽는 중', '읽고싶어요', '관심 없어요']);
    expect(s.row.last.tone, SheetActionTone.danger);
    s.onPrimary!();
    expect(h['read'], 1);
    s.row.last.onTap();
    expect(h['ni'], 1);
  });

  test('찜 — 프라이머리 읽었어요 + 로우(읽는중·찜 해제)', () {
    final s = _spec(_ub(BookStatus.wantToRead), {});
    expect(s.primaryLabel, '읽었어요');
    expect(s.row.map((a) => a.label).toList(), ['읽는 중', '찜 해제']);
    expect(s.row.every((a) => a.tone == SheetActionTone.neutral), isTrue);
  });

  test('읽는 중 — 프라이머리 다 읽었어요 + 로우(삭제[danger]만)', () {
    final s = _spec(_ub(BookStatus.reading), {});
    expect(s.primaryLabel, '다 읽었어요');
    expect(s.row.map((a) => a.label).toList(), ['삭제']);
    expect(s.row.single.tone, SheetActionTone.danger);
  });

  test('읽은 책 평가無 — 프라이머리 평가 남기기 + 로우(되돌리기·삭제)', () {
    final h = <String, int>{};
    final s = _spec(_ub(BookStatus.read), h);
    expect(s.primaryLabel, '평가 남기기');
    expect(s.showRatingCard, isFalse);
    expect(s.row.map((a) => a.label).toList(), ['되돌리기', '삭제']);
    s.row.first.onTap();
    expect(h['revert'], 1);
    s.onPrimary!();
    expect(h['feedback'], 1);
  });

  test('읽은 책 평가有 — 카드 노출(primary 없음) + 로우(되돌리기·삭제)', () {
    final s = _spec(_ub(BookStatus.read, rating: 'good'), {});
    expect(s.showRatingCard, isTrue);
    expect(s.primaryLabel, isNull);
    expect(s.onPrimary, isNull);
    expect(s.row.map((a) => a.label).toList(), ['되돌리기', '삭제']);
  });
}
