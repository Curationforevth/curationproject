import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/models/book.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/core/models/feedback.dart';
import 'package:curation_app/core/models/user_profile.dart';
import 'package:curation_app/core/models/emotion_tag.dart';
import 'package:curation_app/core/models/reflection_prompt.dart';

void main() {
  group('Book', () {
    test('fromJson/toJson roundtrip', () {
      final json = {
        'id': '123',
        'isbn': '9788936434267',
        'title': '채식주의자',
        'author': '한강',
        'publisher': '창비',
        'cover_url': 'https://example.com/cover.jpg',
        'page_count': 247,
        'description': '소설',
        'genre': '한국소설',
        'source': 'aladin',
        'source_id': 'ext-123',
        'created_at': '2026-03-22T10:00:00Z',
      };

      final book = Book.fromJson(json);
      expect(book.title, '채식주의자');
      expect(book.author, '한강');
      expect(book.pageCount, 247);
      expect(book.source, 'aladin');

      final output = book.toJson();
      expect(output['title'], '채식주의자');
      expect(output['cover_url'], 'https://example.com/cover.jpg');
      expect(output['page_count'], 247);
    });

    test('toJsonForUpsert excludes id and createdAt', () {
      final book = Book(
        id: 'temp-id',
        isbn: '9788936434267',
        title: '채식주의자',
        author: '한강',
        source: 'kakao',
      );

      final json = book.toJsonForUpsert();
      expect(json.containsKey('id'), false);
      expect(json.containsKey('created_at'), false);
      expect(json['isbn'], '9788936434267');
      expect(json['title'], '채식주의자');
      expect(json['source'], 'kakao');
    });
  });

  group('UserBook', () {
    test('fromJson with status enum', () {
      final json = {
        'id': 'ub-1',
        'user_id': 'user-1',
        'book_id': 'book-1',
        'status': 'reading',
        'created_at': '2026-03-22T10:00:00Z',
        'updated_at': '2026-03-22T10:00:00Z',
      };

      final userBook = UserBook.fromJson(json);
      expect(userBook.status, BookStatus.reading);
      expect(userBook.book, isNull);
    });

    test('fromJson with joined book', () {
      final json = {
        'id': 'ub-1',
        'user_id': 'user-1',
        'book_id': 'book-1',
        'status': 'read',
        'created_at': '2026-03-22T10:00:00Z',
        'updated_at': '2026-03-22T10:00:00Z',
        'books': {
          'id': 'book-1',
          'title': '채식주의자',
          'author': '한강',
        },
      };

      final userBook = UserBook.fromJson(json);
      expect(userBook.book, isNotNull);
      expect(userBook.book!.title, '채식주의자');
    });

    test('toJson outputs snake_case status', () {
      final userBook = UserBook(
        id: 'ub-1',
        userId: 'user-1',
        bookId: 'book-1',
        status: BookStatus.reading,
      );
      expect(userBook.toJson()['status'], 'reading');
    });

    test('fromJson parses rating, emotionTags, reviewText', () {
      final json = {
        'id': 'ub-1',
        'user_id': 'u-1',
        'book_id': 'b-1',
        'status': 'reading',
        'shelf_order': null,
        'rating': 'good',
        'emotion_tags': ['tag-id-1', 'tag-id-2'],
        'review_text': '정말 좋은 책이었다',
        'created_at': null,
        'updated_at': null,
      };

      final ub = UserBook.fromJson(json);
      expect(ub.rating, 'good');
      expect(ub.emotionTags, ['tag-id-1', 'tag-id-2']);
      expect(ub.reviewText, '정말 좋은 책이었다');
    });

    test('fromJson handles null feedback fields', () {
      final json = {
        'id': 'ub-2',
        'user_id': 'u-1',
        'book_id': 'b-1',
        'status': 'read',
        'shelf_order': null,
        'created_at': null,
        'updated_at': null,
      };

      final ub = UserBook.fromJson(json);
      expect(ub.rating, isNull);
      expect(ub.emotionTags, isNull);
      expect(ub.reviewText, isNull);
    });
  });

  group('BookFeedback', () {
    test('fromJson/toJson roundtrip', () {
      final json = {
        'id': 'fb-1',
        'user_book_id': 'ub-1',
        'category': 'writing_style',
        'sentiment': 'positive',
        'free_text': '문체가 아름다워요',
        'created_at': '2026-03-22T10:00:00Z',
      };

      final feedback = BookFeedback.fromJson(json);
      expect(feedback.category, FeedbackCategory.writingStyle);
      expect(feedback.sentiment, FeedbackSentiment.positive);
      expect(feedback.freeText, '문체가 아름다워요');

      final output = feedback.toJson();
      expect(output['category'], 'writing_style');
      expect(output['sentiment'], 'positive');
    });
  });

  group('UserProfile', () {
    test('fromJson/toJson roundtrip', () {
      final json = {
        'id': 'user-1',
        'email': 'test@example.com',
        'nickname': '독서왕',
        'avatar_url': 'https://example.com/avatar.jpg',
        'created_at': '2026-03-22T10:00:00Z',
      };

      final profile = UserProfile.fromJson(json);
      expect(profile.nickname, '독서왕');

      final output = profile.toJson();
      expect(output['avatar_url'], 'https://example.com/avatar.jpg');
    });
  });

  group('EmotionTag', () {
    test('fromJson parses correctly', () {
      final json = {
        'id': 'et-1',
        'label': '잔잔한',
        'sort_order': 1,
        'is_active': true,
      };

      final tag = EmotionTag.fromJson(json);
      expect(tag.id, 'et-1');
      expect(tag.label, '잔잔한');
      expect(tag.sortOrder, 1);
      expect(tag.isActive, true);
    });
  });

  group('ReflectionPrompt', () {
    test('fromJson parses with category', () {
      final json = {
        'id': 'rp-1',
        'question': '주인공의 어떤 선택이 인상적이었나요?',
        'category': 'character',
        'is_active': true,
      };

      final prompt = ReflectionPrompt.fromJson(json);
      expect(prompt.id, 'rp-1');
      expect(prompt.question, '주인공의 어떤 선택이 인상적이었나요?');
      expect(prompt.category, 'character');
    });

    test('fromJson handles null category', () {
      final json = {
        'id': 'rp-2',
        'question': '가장 기억에 남는 장면이 있나요?',
        'category': null,
        'is_active': true,
      };

      final prompt = ReflectionPrompt.fromJson(json);
      expect(prompt.category, isNull);
    });
  });
}
