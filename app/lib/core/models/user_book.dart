import 'book.dart';

enum BookStatus {
  read,
  reading,
  wantToRead;

  String toJson() {
    switch (this) {
      case BookStatus.read:
        return 'read';
      case BookStatus.reading:
        return 'reading';
      case BookStatus.wantToRead:
        return 'want_to_read';
    }
  }

  static BookStatus fromJson(String value) {
    switch (value) {
      case 'read':
        return BookStatus.read;
      case 'reading':
        return BookStatus.reading;
      case 'want_to_read':
        return BookStatus.wantToRead;
      default:
        throw ArgumentError('Unknown BookStatus: $value');
    }
  }
}

class UserBook {
  final String id;
  final String userId;
  final String bookId;
  final BookStatus status;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final Book? book;

  const UserBook({
    required this.id,
    required this.userId,
    required this.bookId,
    required this.status,
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

  Map<String, dynamic> toJson() {
    return {
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
    };
  }
}
