import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/book.dart';

class BookSearchService {
  final String _apiKey = dotenv.env['KAKAO_REST_API_KEY']!;
  final SupabaseClient _supabase = Supabase.instance.client;

  static const _baseUrl = 'https://dapi.kakao.com/v3/search/book';

  /// 카카오 책 검색 API 호출
  Future<List<Book>> search(String query, {int page = 1, int size = 20}) async {
    if (query.trim().isEmpty) return [];

    final uri = Uri.parse(_baseUrl).replace(queryParameters: {
      'query': query,
      'page': page.toString(),
      'size': size.toString(),
    });

    final response = await http.get(
      uri,
      headers: {'Authorization': 'KakaoAK $_apiKey'},
    );

    if (response.statusCode != 200) {
      throw Exception('카카오 책 검색 실패: ${response.statusCode}');
    }

    final data = jsonDecode(response.body) as Map<String, dynamic>;
    final documents = data['documents'] as List<dynamic>;

    return documents
        .map((doc) => _documentToBook(doc as Map<String, dynamic>))
        .toList();
  }

  /// 검색 결과를 books 테이블에 캐싱 (ISBN 기준 upsert)
  Future<void> cacheBook(Book book) async {
    if (book.isbn == null || book.isbn!.isEmpty) return;

    await _supabase.from('books').upsert(
      book.toJson(),
      onConflict: 'isbn',
    );
  }

  /// 카카오 API 응답 → Book 모델 변환
  Book _documentToBook(Map<String, dynamic> doc) {
    // 카카오 API의 isbn 필드: "공백으로 구분된 ISBN10 ISBN13" 형식
    final isbnRaw = doc['isbn'] as String? ?? '';
    final isbns = isbnRaw.split(' ').where((s) => s.isNotEmpty).toList();
    // ISBN13 우선, 없으면 ISBN10
    final isbn = isbns.length >= 2 ? isbns[1] : (isbns.isNotEmpty ? isbns[0] : null);

    final authors = (doc['authors'] as List<dynamic>?)?.cast<String>() ?? [];

    return Book(
      id: '', // Supabase에서 자동 생성
      isbn: isbn,
      title: doc['title'] as String? ?? '',
      author: authors.join(', '),
      publisher: doc['publisher'] as String?,
      coverUrl: doc['thumbnail'] as String?,
      description: doc['contents'] as String?,
      source: 'kakao',
    );
  }
}
