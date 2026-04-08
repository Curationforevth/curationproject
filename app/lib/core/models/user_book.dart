import 'book.dart';

/// JSON values map to v3 user_books.status: finished/reading/wishlist. Enum names kept for source-stability.
enum BookStatus {
  read,
  reading,
  wantToRead;

  String toJson() {
    switch (this) {
      case BookStatus.read:
        return 'finished';
      case BookStatus.reading:
        return 'reading';
      case BookStatus.wantToRead:
        return 'wishlist';
    }
  }

  static BookStatus fromJson(String value) {
    switch (value) {
      case 'finished':
      case 'read': // backward compat
        return BookStatus.read;
      case 'reading':
        return BookStatus.reading;
      case 'wishlist':
      case 'want_to_read': // backward compat
        return BookStatus.wantToRead;
      default:
        return BookStatus.read;
    }
  }
}

class UserBook {
  final String id;
  final String userId;
  final String bookId;
  final BookStatus status;

  /// 서가 뷰 드래그 정렬 순서
  final int? shelfOrder;

  /// 호오 평가 ('good' | 'bad' | null)
  final String? rating;

  /// 감성태그 ID 배열
  final List<String>? emotionTags;

  /// 자유 리뷰 텍스트
  final String? reviewText;

  final DateTime? createdAt;
  final DateTime? updatedAt;
  final Book? book;

  const UserBook({
    required this.id,
    required this.userId,
    required this.bookId,
    required this.status,
    this.shelfOrder,
    this.rating,
    this.emotionTags,
    this.reviewText,
    this.createdAt,
    this.updatedAt,
    this.book,
  });

  factory UserBook.fromJson(Map<String, dynamic> json) {
    return UserBook(
      id: json['id'] as String,
      userId: json['user_id'] as String,
      bookId: json['book_id'] as String,
      status: BookStatus.fromJson(json['status'] as String),
      shelfOrder: json['shelf_order'] as int?,
      rating: json['rating'] as String?,
      emotionTags: (json['emotion_tags'] as List<dynamic>?)
          ?.map((e) => e as String)
          .toList(),
      reviewText: json['review_text'] as String?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : null,
      updatedAt: json['updated_at'] != null
          ? DateTime.parse(json['updated_at'] as String)
          : null,
      book: json['books'] != null
          ? Book.fromJson(json['books'] as Map<String, dynamic>)
          : null,
    );
  }

  UserBook copyWith({
    String? id,
    String? userId,
    String? bookId,
    BookStatus? status,
    int? shelfOrder,
    String? rating,
    List<String>? emotionTags,
    String? reviewText,
    DateTime? createdAt,
    DateTime? updatedAt,
    Book? book,
  }) {
    return UserBook(
      id: id ?? this.id,
      userId: userId ?? this.userId,
      bookId: bookId ?? this.bookId,
      status: status ?? this.status,
      shelfOrder: shelfOrder ?? this.shelfOrder,
      rating: rating ?? this.rating,
      emotionTags: emotionTags ?? this.emotionTags,
      reviewText: reviewText ?? this.reviewText,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      book: book ?? this.book,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
      'shelf_order': shelfOrder,
      'rating': rating,
      'emotion_tags': emotionTags,
      'review_text': reviewText,
    };
  }
}
