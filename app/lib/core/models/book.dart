class Book {
  final String id;
  final String? isbn;
  final String title;
  final String? author;
  final String? publisher;
  final String? coverUrl;
  final int? pageCount;
  final String? description;
  final String? genre;
  final String? source;
  final String? sourceId;

  /// 표지에서 추출한 dominant color 2~3개 (hex 문자열)
  final List<String>? dominantColors;

  /// LLM이 자동 부여한 무드 태그 (예: "잔잔한", "몰입감 있는")
  final List<String>? moodTags;

  /// 장르/무드 기반 자동 배정된 책등 폰트명 (예: "Nanum Myeongjo")
  final String? spineFont;

  final DateTime? createdAt;

  const Book({
    required this.id,
    this.isbn,
    required this.title,
    this.author,
    this.publisher,
    this.coverUrl,
    this.pageCount,
    this.description,
    this.genre,
    this.source,
    this.sourceId,
    this.dominantColors,
    this.moodTags,
    this.spineFont,
    this.createdAt,
  });

  factory Book.fromJson(Map<String, dynamic> json) {
    return Book(
      id: json['id'] as String,
      isbn: json['isbn'] as String?,
      title: json['title'] as String,
      author: json['author'] as String?,
      publisher: json['publisher'] as String?,
      coverUrl: json['cover_url'] as String?,
      pageCount: json['page_count'] as int?,
      description: json['description'] as String?,
      genre: json['genre'] as String?,
      source: json['source'] as String?,
      sourceId: json['source_id'] as String?,
      dominantColors: (json['dominant_colors'] as List<dynamic>?)
          ?.map((e) => e as String)
          .toList(),
      moodTags: (json['mood_tags'] as List<dynamic>?)
          ?.map((e) => e as String)
          .toList(),
      spineFont: json['spine_font'] as String?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'isbn': isbn,
      'title': title,
      'author': author,
      'publisher': publisher,
      'cover_url': coverUrl,
      'page_count': pageCount,
      'description': description,
      'genre': genre,
      'source': source,
      'source_id': sourceId,
      'dominant_colors': dominantColors,
      'mood_tags': moodTags,
      'spine_font': spineFont,
    };
  }

  /// Supabase upsert용 (id, created_at 제외 — DB가 자동 생성)
  Map<String, dynamic> toJsonForUpsert() {
    return {
      'isbn': isbn,
      'title': title,
      'author': author,
      'publisher': publisher,
      'cover_url': coverUrl,
      'page_count': pageCount,
      'description': description,
      'genre': genre,
      'source': source,
      'source_id': sourceId,
      'dominant_colors': dominantColors,
      'mood_tags': moodTags,
      'spine_font': spineFont,
    };
  }
}
