// app/lib/features/book_detail/providers/book_detail_provider.dart
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/models/emotion_tag.dart';
import '../../../core/models/reflection_prompt.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../home/providers/recommendation_provider.dart';

// --- 상태 ---

// Sentinel object used to distinguish "not passed" from explicit null in copyWith.
const _kClear = Object();

class BookDetailState {
  final UserBook? userBook;
  final bool isLoading;
  final bool isSaving;
  final String? error;

  const BookDetailState({
    this.userBook,
    this.isLoading = true,
    this.isSaving = false,
    this.error,
  });

  BookDetailState copyWith({
    UserBook? userBook,
    bool? isLoading,
    bool? isSaving,
    Object? error = _kClear,
  }) {
    return BookDetailState(
      userBook: userBook ?? this.userBook,
      isLoading: isLoading ?? this.isLoading,
      isSaving: isSaving ?? this.isSaving,
      error: identical(error, _kClear) ? this.error : error as String?,
    );
  }
}

// --- Notifier ---

class BookDetailNotifier extends StateNotifier<BookDetailState> {
  final SupabaseClient _supabase;
  final Ref _ref;
  final String _userBookId;

  BookDetailNotifier(this._ref, this._userBookId, [SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client,
        super(const BookDetailState()) {
    _load();
  }

  Future<void> _load() async {
    try {
      final response = await _supabase
          .from('user_books')
          .select('*, books(*)')
          .eq('id', _userBookId)
          .single();

      state = BookDetailState(
        userBook: UserBook.fromJson(response),
        isLoading: false,
      );
    } catch (e) {
      state = BookDetailState(isLoading: false, error: e.toString());
    }
  }

  /// 호오 평가 변경 (낙관적 업데이트)
  Future<void> updateRating(String rating) async {
    final prev = state.userBook;
    if (prev == null || state.isSaving) return;
    state = state.copyWith(isSaving: true);

    // 같은 값이면 해제 (토글)
    final newRating = prev.rating == rating ? null : rating;

    // 낙관적 UI 업데이트
    state = state.copyWith(
      userBook: UserBook(
        id: prev.id,
        userId: prev.userId,
        bookId: prev.bookId,
        status: prev.status,
        shelfOrder: prev.shelfOrder,
        rating: newRating,
        emotionTags: prev.emotionTags,
        reviewText: prev.reviewText,
        createdAt: prev.createdAt,
        updatedAt: prev.updatedAt,
        book: prev.book,
      ),
    );

    try {
      await _supabase
          .from('user_books')
          .update({'rating': newRating})
          .eq('id', _userBookId);
      _ref.invalidate(bookshelfProvider);
      // 추천/홈피드는 당겨서 새로고침에서만 갱신(세션 중 자동 리뉴얼 안 함).
    } catch (e) {
      state = state.copyWith(userBook: prev);
      debugPrint('rating 저장 실패: $e');
      rethrow;
    } finally {
      state = state.copyWith(isSaving: false);
    }
  }

  /// 감성태그 토글 (낙관적 업데이트)
  Future<void> toggleEmotionTag(String tagId) async {
    final prev = state.userBook;
    if (prev == null || state.isSaving) return;
    state = state.copyWith(isSaving: true);

    final currentTags = List<String>.from(prev.emotionTags ?? []);
    if (currentTags.contains(tagId)) {
      currentTags.remove(tagId);
    } else {
      currentTags.add(tagId);
    }
    final newTags = currentTags.isEmpty ? null : currentTags;

    // 낙관적 UI 업데이트
    state = state.copyWith(
      userBook: UserBook(
        id: prev.id,
        userId: prev.userId,
        bookId: prev.bookId,
        status: prev.status,
        shelfOrder: prev.shelfOrder,
        rating: prev.rating,
        emotionTags: newTags,
        reviewText: prev.reviewText,
        createdAt: prev.createdAt,
        updatedAt: prev.updatedAt,
        book: prev.book,
      ),
    );

    try {
      await _supabase
          .from('user_books')
          .update({'emotion_tags': newTags})
          .eq('id', _userBookId);
      _ref.invalidate(bookshelfProvider);
    } catch (e) {
      state = state.copyWith(userBook: prev);
      debugPrint('감성태그 저장 실패: $e');
      rethrow;
    } finally {
      state = state.copyWith(isSaving: false);
    }
  }

  /// 리뷰 텍스트 저장 (명시적, 저장 버튼)
  Future<void> saveReviewText(String text) async {
    final prev = state.userBook;
    if (prev == null || state.isSaving) return;
    state = state.copyWith(isSaving: true);

    final reviewText = text.trim().isEmpty ? null : text.trim();

    try {
      await _supabase
          .from('user_books')
          .update({'review_text': reviewText})
          .eq('id', _userBookId);

      state = state.copyWith(
        userBook: UserBook(
          id: prev.id,
          userId: prev.userId,
          bookId: prev.bookId,
          status: prev.status,
          shelfOrder: prev.shelfOrder,
          rating: prev.rating,
          emotionTags: prev.emotionTags,
          reviewText: reviewText,
          createdAt: prev.createdAt,
          updatedAt: prev.updatedAt,
          book: prev.book,
        ),
      );
      _ref.invalidate(bookshelfProvider);
      // 추천/홈피드는 당겨서 새로고침에서만 갱신(세션 중 자동 리뉴얼 안 함).
    } catch (e) {
      debugPrint('리뷰 저장 실패: $e');
      rethrow;
    } finally {
      state = state.copyWith(isSaving: false);
    }
  }

  /// 읽기 상태 변경
  Future<void> updateStatus(BookStatus newStatus) async {
    final prev = state.userBook;
    if (prev == null || state.isSaving) return;
    state = state.copyWith(isSaving: true);

    state = state.copyWith(
      userBook: UserBook(
        id: prev.id,
        userId: prev.userId,
        bookId: prev.bookId,
        status: newStatus,
        shelfOrder: prev.shelfOrder,
        rating: prev.rating,
        emotionTags: prev.emotionTags,
        reviewText: prev.reviewText,
        createdAt: prev.createdAt,
        updatedAt: prev.updatedAt,
        book: prev.book,
      ),
    );

    try {
      await _supabase
          .from('user_books')
          .update({'status': newStatus.toJson()})
          .eq('id', _userBookId);
      _ref.invalidate(bookshelfProvider);
    } catch (e) {
      state = state.copyWith(userBook: prev);
      debugPrint('상태 변경 실패: $e');
      rethrow;
    } finally {
      state = state.copyWith(isSaving: false);
    }
  }
}

// --- Providers ---

final bookDetailProvider = StateNotifierProvider.family<
    BookDetailNotifier, BookDetailState, String>((ref, userBookId) {
  return BookDetailNotifier(ref, userBookId);
});

/// 감성태그 옵션 (Supabase에서 조회, 앱 세션 동안 캐시)
final emotionTagOptionsProvider = FutureProvider<List<EmotionTag>>((ref) async {
  final response = await Supabase.instance.client
      .from('emotion_tag_options')
      .select()
      .eq('is_active', true)
      .order('sort_order');

  return (response as List<dynamic>)
      .map((json) => EmotionTag.fromJson(json as Map<String, dynamic>))
      .toList();
});

/// 리플렉션 질문 (Supabase에서 조회)
final reflectionPromptsProvider =
    FutureProvider<List<ReflectionPrompt>>((ref) async {
  final response = await Supabase.instance.client
      .from('reflection_prompts')
      .select()
      .eq('is_active', true);

  return (response as List<dynamic>)
      .map((json) => ReflectionPrompt.fromJson(json as Map<String, dynamic>))
      .toList();
});
