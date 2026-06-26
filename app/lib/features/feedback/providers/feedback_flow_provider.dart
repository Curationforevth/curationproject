// app/lib/features/feedback/providers/feedback_flow_provider.dart
import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/impression_logger.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../home/providers/recommendation_provider.dart';

// --- 상태 ---

class FeedbackFlowState {
  final UserBook? userBook;
  final bool isLoading;
  final bool isSaving;
  final String? rating; // 'good' | 'bad' | null
  final List<String> selectedTags;
  final String reviewText;
  final String? error;

  const FeedbackFlowState({
    this.userBook,
    this.isLoading = true,
    this.isSaving = false,
    this.rating,
    this.selectedTags = const [],
    this.reviewText = '',
    this.error,
  });

  static const _sentinel = Object();

  FeedbackFlowState copyWith({
    UserBook? userBook,
    bool? isLoading,
    bool? isSaving,
    Object? rating = _sentinel,
    List<String>? selectedTags,
    String? reviewText,
    String? error,
  }) {
    return FeedbackFlowState(
      userBook: userBook ?? this.userBook,
      isLoading: isLoading ?? this.isLoading,
      isSaving: isSaving ?? this.isSaving,
      rating: rating == _sentinel ? this.rating : rating as String?,
      selectedTags: selectedTags ?? this.selectedTags,
      reviewText: reviewText ?? this.reviewText,
      error: error ?? this.error,
    );
  }
}

// --- Notifier ---

class FeedbackFlowNotifier extends StateNotifier<FeedbackFlowState> {
  final SupabaseClient _supabase;
  final Ref _ref;
  final String _userBookId;

  FeedbackFlowNotifier(this._ref, this._userBookId, [SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client,
        super(const FeedbackFlowState()) {
    _load();
  }

  Future<void> _load() async {
    try {
      final response = await _supabase
          .from('user_books')
          .select('*, books(*)')
          .eq('id', _userBookId)
          .single();

      final userBook = UserBook.fromJson(response);

      state = FeedbackFlowState(
        userBook: userBook,
        isLoading: false,
        // 기존 값이 있으면 초기값으로 채움
        rating: userBook.rating,
        selectedTags: userBook.emotionTags ?? [],
        reviewText: userBook.reviewText ?? '',
      );
    } catch (e) {
      state = FeedbackFlowState(isLoading: false, error: e.toString());
    }
  }

  void setRating(String rating) {
    // 같은 값 탭하면 해제 (토글)
    final newRating = state.rating == rating ? null : rating;
    state = state.copyWith(rating: newRating);
  }

  void toggleTag(String tag) {
    final current = List<String>.from(state.selectedTags);
    if (current.contains(tag)) {
      current.remove(tag);
    } else {
      current.add(tag);
    }
    state = state.copyWith(selectedTags: current);
  }

  void setReviewText(String text) {
    state = state.copyWith(reviewText: text);
  }

  /// 완료 — 전체 저장 후 bookshelfProvider 무효화
  Future<void> submit() async {
    if (state.isSaving) return;
    state = state.copyWith(isSaving: true);

    try {
      final reviewText =
          state.reviewText.trim().isEmpty ? null : state.reviewText.trim();
      final tags =
          state.selectedTags.isEmpty ? null : state.selectedTags;

      await _supabase.from('user_books').update({
        'rating': state.rating,
        'emotion_tags': tags,
        'review_text': reviewText,
      }).eq('id', _userBookId);

      final bookId = state.userBook?.bookId;
      if (bookId != null) {
        if (state.rating == 'good') {
          unawaited(
            ImpressionLogger(_supabase).logAction(bookId: bookId, action: 'liked'),
          );
        } else if (state.rating == 'bad') {
          unawaited(
            ImpressionLogger(_supabase).logAction(bookId: bookId, action: 'disliked'),
          );
        }
      }

      _ref.invalidate(bookshelfProvider);
      // 피드백이 user_books 를 바꿨으므로 추천도 다시 가져와야 한다(서버가
      // 다음 /recommend 호출에서 새 input_hash 로 재계산). 과거: bookshelf 만
      // 무효화하고 recommendationsProvider 는 안 해 추천이 세션 내내 안 바뀜.
      _ref.invalidate(recommendationsProvider);
    } catch (e) {
      debugPrint('피드백 저장 실패: $e');
      state = state.copyWith(isSaving: false);
      rethrow;
    }

    state = state.copyWith(isSaving: false);
  }

  /// 나중에 — 현재 입력된 내용 저장 후 종료
  Future<void> skip() async {
    // 입력된 값이 있으면 저장, 없으면 그냥 종료
    final hasAnyInput = state.rating != null ||
        state.selectedTags.isNotEmpty ||
        state.reviewText.trim().isNotEmpty;

    if (hasAnyInput) {
      await submit();
    }
  }
}

// --- Provider ---

final feedbackFlowProvider = StateNotifierProvider.family<FeedbackFlowNotifier,
    FeedbackFlowState, String>((ref, userBookId) {
  return FeedbackFlowNotifier(ref, userBookId);
});
