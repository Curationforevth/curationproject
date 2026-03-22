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
    };
  }
}
