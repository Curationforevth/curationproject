import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/book.dart';

class RecommendedBook {
  final String bookId;
  final double score;
  final String title;
  final String author;
  final String? coverUrl;

  const RecommendedBook({
    required this.bookId,
    required this.score,
    required this.title,
    required this.author,
    this.coverUrl,
  });

  factory RecommendedBook.fromJson(Map<String, dynamic> json) =>
      RecommendedBook(
        bookId: json['book_id'] as String,
        score: (json['score'] as num).toDouble(),
        title: json['title'] as String,
        author: json['author'] as String,
        coverUrl: json['cover_url'] as String?,
      );

  Book toBook() => Book(
        id: bookId,
        title: title,
        author: author,
        coverUrl: coverUrl,
      );
}

class RecommendationResult {
  final List<RecommendedBook> recommendations;
  final bool hasFeedback;
  final int totalLiked;

  const RecommendationResult({
    required this.recommendations,
    required this.hasFeedback,
    required this.totalLiked,
  });
}

/// /home 섹션의 책 (personal_recommend 외엔 score 없음 → score 무시).
class HomeBook {
  final String bookId;
  final String title;
  final String author;
  final String? coverUrl;

  const HomeBook({
    required this.bookId,
    required this.title,
    required this.author,
    this.coverUrl,
  });

  factory HomeBook.fromJson(Map<String, dynamic> json) => HomeBook(
        bookId: json['book_id'] as String,
        title: (json['title'] ?? '') as String,
        author: (json['author'] ?? '') as String,
        coverUrl: json['cover_url'] as String?,
      );

  Book toBook() => Book(
        id: bookId,
        title: title,
        author: author,
        coverUrl: coverUrl,
      );
}

/// /home 의 한 섹션 (type: personal_recommend / curation / similar / trending / category_nav).
class HomeSection {
  final String type;
  final String title;
  final List<HomeBook> books;

  const HomeSection({
    required this.type,
    required this.title,
    required this.books,
  });

  factory HomeSection.fromJson(Map<String, dynamic> json) => HomeSection(
        type: (json['type'] ?? '') as String,
        title: (json['title'] ?? '') as String,
        books: ((json['books'] as List<dynamic>?) ?? const [])
            .map((e) => HomeBook.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class HomeFeed {
  final List<HomeSection> sections;
  final String? cta;

  const HomeFeed({required this.sections, this.cta});
}

class RecommendationService {
  static String get _baseUrl =>
      dotenv.env['RECOMMENDATION_SERVER_URL'] ??
      'https://curation-recommendation.onrender.com';

  // Render free tier cold-start 대응: 30초 타임아웃
  static const _timeout = Duration(seconds: 30);

  String? get _token =>
      Supabase.instance.client.auth.currentSession?.accessToken;
  String? get _userId => Supabase.instance.client.auth.currentUser?.id;

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $_token',
        'Content-Type': 'application/json',
      };

  /// 유저 맞춤 추천
  Future<RecommendationResult> getRecommendations({int limit = 10}) async {
    final userId = _userId;
    if (userId == null || _token == null) throw Exception('Not authenticated');

    final uri = Uri.parse('$_baseUrl/recommend/$userId?limit=$limit');
    final response =
        await http.get(uri, headers: _headers).timeout(_timeout);

    if (response.statusCode == 200) {
      final json = jsonDecode(response.body) as Map<String, dynamic>;
      final books = (json['recommendations'] as List<dynamic>)
          .map((e) => RecommendedBook.fromJson(e as Map<String, dynamic>))
          .toList();
      final meta = json['meta'] as Map<String, dynamic>;
      return RecommendationResult(
        recommendations: books,
        hasFeedback: meta['has_feedback'] as bool,
        totalLiked: meta['total_liked'] as int,
      );
    }
    throw Exception('Recommendation failed: ${response.statusCode}');
  }

  /// 비슷한 책
  Future<List<RecommendedBook>> getSimilarBooks(
    String bookId, {
    int limit = 10,
  }) async {
    if (_token == null) throw Exception('Not authenticated');

    final uri = Uri.parse('$_baseUrl/similar/$bookId?limit=$limit');
    final response =
        await http.get(uri, headers: _headers).timeout(_timeout);

    if (response.statusCode == 200) {
      final json = jsonDecode(response.body) as Map<String, dynamic>;
      return (json['similar'] as List<dynamic>)
          .map((e) => RecommendedBook.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    if (response.statusCode == 404) return []; // book not in index
    throw Exception('Similar failed: ${response.statusCode}');
  }

  /// 홈 피드 — 큐레이션/트렌딩/맞춤추천/비슷한책 섹션을 한 번에 받는다.
  /// (서버가 빈 섹션은 제거하고, 한 쿼리 실패해도 가능한 섹션만 돌려줌.)
  Future<HomeFeed> getHome() async {
    final userId = _userId;
    if (userId == null || _token == null) throw Exception('Not authenticated');

    final uri = Uri.parse('$_baseUrl/home/$userId');
    final response =
        await http.get(uri, headers: _headers).timeout(_timeout);

    if (response.statusCode == 200) {
      final json = jsonDecode(response.body) as Map<String, dynamic>;
      final sections = ((json['sections'] as List<dynamic>?) ?? const [])
          .map((e) => HomeSection.fromJson(e as Map<String, dynamic>))
          .toList();
      return HomeFeed(sections: sections, cta: json['cta'] as String?);
    }
    throw Exception('Home failed: ${response.statusCode}');
  }
}
