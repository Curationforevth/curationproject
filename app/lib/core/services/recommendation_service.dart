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
}
