import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/book.dart';
import '../models/user_book.dart';
import '../utils/color_extractor.dart';
import '../utils/font_assigner.dart';

class BookRegistrationService {
  final SupabaseClient _supabase;

  BookRegistrationService([SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client;

  /// 책 등록 파이프라인 (동기 + 비동기 백그라운드)
  Future<String> registerBook(Book book, BookStatus status) async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) throw Exception('로그인이 필요합니다');

    // 1. books upsert — id 제외하여 DB가 UUID 자동 생성
    final upsertData = book.toJsonForUpsert();
    final List<dynamic> upsertResult;

    if (book.isbn != null && book.isbn!.isNotEmpty) {
      upsertResult = await _supabase
          .from('books')
          .upsert(upsertData, onConflict: 'isbn')
          .select('id');
    } else {
      upsertResult = await _supabase
          .from('books')
          .insert(upsertData)
          .select('id');
    }

    final bookId = upsertResult.first['id'] as String;

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
