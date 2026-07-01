import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/book.dart';
import '../models/user_book.dart';
import '../utils/color_extractor.dart';
import '../utils/font_assigner.dart';

/// 등록 시 `books` 행을 어떻게 식별할지 결정하는 순수 전략.
///
/// 화차 중복 버그의 근본 수정 지점:
/// - 추천/홈/비슷한책은 `RecommendedBook.toBook()`/`HomeBook.toBook()`로 오며
///   **기존 `books.id`는 싣지만 isbn·source는 null**이다. 이때 id를 무시하고 isbn만
///   보면 insert 경로로 null-isbn 복제행을 새로 만들어 같은 책이 중복 등록된다.
///   → id가 있으면 그 행을 그대로 재사용해야 한다.
/// - 외부 검색(카카오)은 id가 비어있고 isbn이 있다 → isbn 기준 upsert.
enum BookRef { reuseId, upsertByIsbn, insertNew }

@visibleForTesting
BookRef resolveBookRef(Book book) {
  if (book.id.isNotEmpty) return BookRef.reuseId;
  if (book.isbn != null && book.isbn!.isNotEmpty) return BookRef.upsertByIsbn;
  return BookRef.insertNew;
}

class BookRegistrationService {
  final SupabaseClient _supabase;

  BookRegistrationService([SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client;

  /// 책 등록 파이프라인 (동기 + 비동기 백그라운드)
  Future<String> registerBook(Book book, BookStatus status) async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) throw Exception('로그인이 필요합니다');

    // 1. books 행 식별 (resolveBookRef 전략)
    final String bookId;
    switch (resolveBookRef(book)) {
      case BookRef.reuseId:
        // 내부소스(추천/홈/유사)는 이미 존재하는 books 행을 참조 → 그 id 재사용.
        // 새 행(특히 null-isbn 복제행) 생성 금지. 값 보강은 아래 백그라운드가 담당.
        bookId = book.id;
        break;
      case BookRef.upsertByIsbn:
        final r = await _supabase
            .from('books')
            .upsert(book.toJsonForUpsert(), onConflict: 'isbn')
            .select('id');
        bookId = r.first['id'] as String;
        break;
      case BookRef.insertNew:
        bookId = await _insertOrReuseByTitleAuthor(book);
        break;
    }

    // 2. user_books insert
    final userBookResult = await _supabase.from('user_books').insert({
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
    }).select('id');

    final userBookId = userBookResult.first['id'] as String;

    // 3. 비동기 백그라운드 — 실패해도 사용자 흐름 차단하지 않음
    _enrichBookAsync(bookId, book);
    return userBookId;
  }

  /// id·isbn 둘 다 없는 예외 경로: 같은 title+author 행이 있으면 재사용, 없으면 insert.
  /// (null-isbn 복제행 양산 방지 — 최소 방어.)
  Future<String> _insertOrReuseByTitleAuthor(Book book) async {
    final existing = await _supabase
        .from('books')
        .select('id')
        .eq('title', book.title)
        .eq('author', book.author ?? '')
        .limit(1);
    if ((existing as List).isNotEmpty) {
      return existing.first['id'] as String;
    }
    final inserted = await _supabase
        .from('books')
        .insert(book.toJsonForUpsert())
        .select('id');
    return inserted.first['id'] as String;
  }

  /// 유저 서재에 해당 ISBN의 책이 있는지 확인
  Future<bool> isBookInShelf(String? isbn) async {
    if (isbn == null || isbn.isEmpty) return false;

    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) return false;

    final result = await _supabase
        .from('user_books')
        .select('id, books!inner(isbn)')
        .eq('user_id', userId)
        .eq('books.isbn', isbn)
        .limit(1);

    return (result as List).isNotEmpty;
  }

  /// 유저 서재의 모든 ISBN 목록 조회
  Future<Set<String>> getShelfIsbns() async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) return {};

    final result = await _supabase
        .from('user_books')
        .select('books(isbn)')
        .eq('user_id', userId);

    return (result as List)
        .map((row) => row['books']?['isbn'] as String?)
        .whereType<String>()
        .where((isbn) => isbn.isNotEmpty)
        .toSet();
  }

  /// 백그라운드: 색상 추출 + 폰트 배정
  /// DB에 이미 값이 있으면 스킵 (배치 enricher가 먼저 처리한 경우)
  Future<void> _enrichBookAsync(String bookId, Book book) async {
    try {
      // DB에서 현재 상태 확인
      final existing = await _supabase
          .from('books')
          .select('dominant_colors, spine_font')
          .eq('id', bookId)
          .single();

      final updates = <String, dynamic>{};

      // dominant color 추출 (DB에 없을 때만)
      final hasColors = existing['dominant_colors'] != null;
      if (!hasColors && book.coverUrl != null && book.coverUrl!.isNotEmpty) {
        final colors = await ColorExtractor.extractFromUrl(book.coverUrl!);
        if (colors.isNotEmpty) {
          updates['dominant_colors'] = colors;
        }
      }

      // spine font 배정 (DB에 없을 때만)
      final hasFont = existing['spine_font'] != null;
      if (!hasFont) {
        final font = FontAssigner.assignFont(
          genre: book.genre,
          description: book.description,
        );
        updates['spine_font'] = font;
      }

      // DB 업데이트
      if (updates.isNotEmpty) {
        await _supabase.from('books').update(updates).eq('id', bookId);
      }
    } catch (e) {
      debugPrint('책 메타데이터 보강 실패 (bookId: $bookId): $e');
    }
  }
}
