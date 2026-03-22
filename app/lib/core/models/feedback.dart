enum FeedbackCategory {
  character,
  writingStyle,
  worldbuilding,
  plot,
  message,
  atmosphere;

  String toJson() {
    switch (this) {
      case FeedbackCategory.character:
        return 'character';
      case FeedbackCategory.writingStyle:
        return 'writing_style';
      case FeedbackCategory.worldbuilding:
        return 'worldbuilding';
      case FeedbackCategory.plot:
        return 'plot';
      case FeedbackCategory.message:
        return 'message';
      case FeedbackCategory.atmosphere:
        return 'atmosphere';
    }
  }

  static FeedbackCategory fromJson(String value) {
    switch (value) {
      case 'character':
        return FeedbackCategory.character;
      case 'writing_style':
        return FeedbackCategory.writingStyle;
      case 'worldbuilding':
        return FeedbackCategory.worldbuilding;
      case 'plot':
        return FeedbackCategory.plot;
      case 'message':
        return FeedbackCategory.message;
      case 'atmosphere':
        return FeedbackCategory.atmosphere;
      default:
        throw ArgumentError('Unknown FeedbackCategory: $value');
    }
  }
}

enum FeedbackSentiment {
  positive,
  negative;

  String toJson() => name;

  static FeedbackSentiment fromJson(String value) {
    switch (value) {
      case 'positive':
        return FeedbackSentiment.positive;
      case 'negative':
        return FeedbackSentiment.negative;
      default:
        throw ArgumentError('Unknown FeedbackSentiment: $value');
    }
  }
}

class BookFeedback {
  final String id;
  final String userBookId;
  final FeedbackCategory category;
  final FeedbackSentiment sentiment;
  final String? freeText;
  final DateTime? createdAt;

  const BookFeedback({
    required this.id,
    required this.userBookId,
    required this.category,
    required this.sentiment,
    this.freeText,
    this.createdAt,
  });

  factory BookFeedback.fromJson(Map<String, dynamic> json) {
    return BookFeedback(
      id: json['id'] as String,
      userBookId: json['user_book_id'] as String,
      category: FeedbackCategory.fromJson(json['category'] as String),
      sentiment: FeedbackSentiment.fromJson(json['sentiment'] as String),
      freeText: json['free_text'] as String?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'user_book_id': userBookId,
      'category': category.toJson(),
      'sentiment': sentiment.toJson(),
      'free_text': freeText,
    };
  }
}
