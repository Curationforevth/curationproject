# 책 상세 화면 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서재에 등록된 책의 상세 화면을 구현하여 호오 평가, 감성태그, 자유 리뷰를 수집한다.

**Architecture:** BookDetailScreen은 feature-first 구조로 `lib/features/book_detail/`에 배치. BookDetailNotifier(StateNotifier)가 낙관적 업데이트로 호오/감성태그 자동 저장을 처리하고, 실패 시 롤백한다. 감성태그/리플렉션 질문은 Supabase 테이블에서 관리하여 앱 배포 없이 수정 가능.

**Tech Stack:** Flutter, Supabase, Riverpod, GoRouter, 기존 Warm Ink 디자인 시스템

**Spec:** `docs/superpowers/specs/2026-03-23-book-detail-screen-design.md`

---

## 파일 구조

### 신규 파일
| 파일 | 책임 |
|------|------|
| `supabase/005_book_detail.sql` | DB 마이그레이션 (컬럼 추가 + 신규 테이블 + 시드 데이터) |
| `app/lib/core/models/emotion_tag.dart` | EmotionTag 모델 |
| `app/lib/core/models/reflection_prompt.dart` | ReflectionPrompt 모델 |
| `app/lib/features/book_detail/providers/book_detail_provider.dart` | BookDetailNotifier + providers |
| `app/lib/features/book_detail/widgets/rating_selector.dart` | 호오 3단계 선택 위젯 |
| `app/lib/features/book_detail/widgets/emotion_tag_chips.dart` | 감성태그 칩 위젯 |
| `app/lib/features/book_detail/widgets/review_text_section.dart` | 자유 텍스트 + 글쓰기 도움 패널 |
| `app/lib/features/book_detail/screens/book_detail_screen.dart` | 상세 화면 조립 |
| `app/test/book_detail_test.dart` | 모델 + 위젯 테스트 |

### 수정 파일
| 파일 | 변경 내용 |
|------|-----------|
| `app/lib/core/models/user_book.dart` | rating, emotionTags, reviewText 필드 추가 |
| `app/lib/core/services/book_registration_service.dart` | registerBook → userBookId 반환 |
| `app/lib/features/bookshelf/providers/bookshelf_provider.dart` | addBookToShelf → userBookId 반환 |
| `app/lib/features/bookshelf/screens/bookshelf_screen.dart` | BookSpine 탭 → 상세 화면 이동 |
| `app/lib/features/search/screens/book_search_screen.dart` | 스낵바에 "보러가기" 액션 추가 |
| `app/lib/routing/app_router.dart` | `/book/:userBookId` 라우트 추가 |

---

## Task 1: DB 마이그레이션

**Files:**
- Create: `supabase/005_book_detail.sql`

- [ ] **Step 1: 마이그레이션 파일 작성**

```sql
-- supabase/005_book_detail.sql
-- 책 상세 화면: 호오 평가 + 감성태그 + 리뷰 텍스트

-- 1. user_books 컬럼 추가
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS rating text DEFAULT NULL;
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS emotion_tags jsonb DEFAULT NULL;
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS review_text text DEFAULT NULL;

-- rating 값 제약
ALTER TABLE public.user_books ADD CONSTRAINT user_books_rating_check
  CHECK (rating IS NULL OR rating IN ('good', 'neutral', 'bad'));

-- 2. 감성태그 옵션 테이블
CREATE TABLE IF NOT EXISTS public.emotion_tag_options (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label text NOT NULL,
  sort_order int NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE public.emotion_tag_options ENABLE ROW LEVEL SECURITY;

CREATE POLICY "누구나 감성태그 옵션 조회"
  ON public.emotion_tag_options FOR SELECT
  USING (true);

-- 3. 리플렉션 질문 테이블
CREATE TABLE IF NOT EXISTS public.reflection_prompts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question text NOT NULL,
  category text DEFAULT NULL,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE public.reflection_prompts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "누구나 리플렉션 질문 조회"
  ON public.reflection_prompts FOR SELECT
  USING (true);

-- 4. 시드 데이터: 감성태그 옵션
INSERT INTO public.emotion_tag_options (label, sort_order) VALUES
  ('잔잔한', 1),
  ('따뜻한', 2),
  ('긴장감', 3),
  ('몰입', 4),
  ('여운', 5),
  ('유쾌한', 6),
  ('무거운', 7),
  ('서정적', 8),
  ('속도감', 9),
  ('생각할거리', 10);

-- 5. 시드 데이터: 리플렉션 질문
INSERT INTO public.reflection_prompts (question, category) VALUES
  ('가장 기억에 남는 장면이 있나요?', NULL),
  ('이 책을 읽고 떠오른 생각이나 감정이 있다면?', NULL),
  ('누군가에게 이 책을 추천한다면 어떻게 소개할 것 같나요?', NULL),
  ('주인공의 어떤 선택이 인상적이었나요?', 'character'),
  ('마음에 드는 캐릭터가 있었나요?', 'character'),
  ('이 책의 문장이 어떻게 느껴졌나요?', 'writing_style'),
  ('특별히 좋았던 문장이나 표현이 있나요?', 'writing_style'),
  ('이야기의 전개가 어떻게 느껴졌나요?', 'plot'),
  ('예상치 못한 전개가 있었나요?', 'plot'),
  ('이 책이 그리는 세계가 어떻게 느껴졌나요?', 'worldbuilding'),
  ('이 책의 분위기를 한 단어로 표현한다면?', 'atmosphere'),
  ('이 책이 전하는 메시지가 있다면 무엇일까요?', 'message');

-- 6. user_books UPDATE 정책 (rating, emotion_tags, review_text 수정 허용)
CREATE POLICY "유저가 자신의 user_books 업데이트"
  ON public.user_books FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
```

- [ ] **Step 2: 커밋**

```bash
git add supabase/005_book_detail.sql
git commit -m "chore: DB 마이그레이션 005 — 호오/감성태그/리뷰 + 옵션 테이블"
```

---

## Task 2: UserBook 모델 확장

**Files:**
- Modify: `app/lib/core/models/user_book.dart`
- Test: `app/test/models_test.dart`

- [ ] **Step 1: 테스트 작성**

`app/test/models_test.dart`의 `'UserBook'` 그룹 안에 추가:
```dart
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: FAIL — `rating` 등 필드 없음

- [ ] **Step 3: UserBook에 필드 추가**

`app/lib/core/models/user_book.dart` — UserBook 클래스에 필드 추가:
```dart
  /// 호오 평가 ('good', 'neutral', 'bad')
  final String? rating;

  /// 감성태그 ID 배열
  final List<String>? emotionTags;

  /// 자유 리뷰 텍스트
  final String? reviewText;
```

생성자에 추가:
```dart
    this.rating,
    this.emotionTags,
    this.reviewText,
```

`fromJson`에 추가:
```dart
      rating: json['rating'] as String?,
      emotionTags: (json['emotion_tags'] as List<dynamic>?)
          ?.map((e) => e as String)
          .toList(),
      reviewText: json['review_text'] as String?,
```

`toJson`에 추가:
```dart
      'rating': rating,
      'emotion_tags': emotionTags,
      'review_text': reviewText,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: ALL PASS

- [ ] **Step 5: 커밋**

```bash
git add app/lib/core/models/user_book.dart app/test/models_test.dart
git commit -m "feat: UserBook 모델 — rating, emotionTags, reviewText 필드 추가"
```

---

## Task 3: EmotionTag + ReflectionPrompt 모델

**Files:**
- Create: `app/lib/core/models/emotion_tag.dart`
- Create: `app/lib/core/models/reflection_prompt.dart`
- Test: `app/test/models_test.dart`

- [ ] **Step 1: 테스트 작성**

`app/test/models_test.dart`에 추가:
```dart
import 'package:curation_app/core/models/emotion_tag.dart';
import 'package:curation_app/core/models/reflection_prompt.dart';

// main() 안에 추가:

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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: FAIL — import 실패

- [ ] **Step 3: EmotionTag 모델 생성**

```dart
// app/lib/core/models/emotion_tag.dart

class EmotionTag {
  final String id;
  final String label;
  final int sortOrder;
  final bool isActive;

  const EmotionTag({
    required this.id,
    required this.label,
    required this.sortOrder,
    required this.isActive,
  });

  factory EmotionTag.fromJson(Map<String, dynamic> json) {
    return EmotionTag(
      id: json['id'] as String,
      label: json['label'] as String,
      sortOrder: json['sort_order'] as int? ?? 0,
      isActive: json['is_active'] as bool? ?? true,
    );
  }
}
```

- [ ] **Step 4: ReflectionPrompt 모델 생성**

```dart
// app/lib/core/models/reflection_prompt.dart

class ReflectionPrompt {
  final String id;
  final String question;
  final String? category;
  final bool isActive;

  const ReflectionPrompt({
    required this.id,
    required this.question,
    this.category,
    required this.isActive,
  });

  factory ReflectionPrompt.fromJson(Map<String, dynamic> json) {
    return ReflectionPrompt(
      id: json['id'] as String,
      question: json['question'] as String,
      category: json['category'] as String?,
      isActive: json['is_active'] as bool? ?? true,
    );
  }
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: ALL PASS

- [ ] **Step 6: 커밋**

```bash
git add app/lib/core/models/emotion_tag.dart app/lib/core/models/reflection_prompt.dart app/test/models_test.dart
git commit -m "feat: EmotionTag + ReflectionPrompt 모델"
```

---

## Task 4: BookDetailProvider

**Files:**
- Create: `app/lib/features/book_detail/providers/book_detail_provider.dart`

- [ ] **Step 1: Provider 구현**

```dart
// app/lib/features/book_detail/providers/book_detail_provider.dart
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/user_book.dart';
import '../../../core/models/emotion_tag.dart';
import '../../../core/models/reflection_prompt.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';

// --- 상태 ---

class BookDetailState {
  final UserBook? userBook;
  final bool isLoading;
  final String? error;

  const BookDetailState({
    this.userBook,
    this.isLoading = true,
    this.error,
  });

  BookDetailState copyWith({
    UserBook? userBook,
    bool? isLoading,
    String? error,
  }) {
    return BookDetailState(
      userBook: userBook ?? this.userBook,
      isLoading: isLoading ?? this.isLoading,
      error: error,
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
    if (prev == null) return;

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
    } catch (e) {
      // 롤백
      state = state.copyWith(userBook: prev);
      debugPrint('rating 저장 실패: $e');
      rethrow;
    }
  }

  /// 감성태그 토글 (낙관적 업데이트)
  Future<void> toggleEmotionTag(String tagId) async {
    final prev = state.userBook;
    if (prev == null) return;

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
      // 롤백
      state = state.copyWith(userBook: prev);
      debugPrint('감성태그 저장 실패: $e');
      rethrow;
    }
  }

  /// 리뷰 텍스트 저장 (명시적, 저장 버튼)
  Future<void> saveReviewText(String text) async {
    final prev = state.userBook;
    if (prev == null) return;

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
    } catch (e) {
      debugPrint('리뷰 저장 실패: $e');
      rethrow;
    }
  }

  /// 읽기 상태 변경
  Future<void> updateStatus(BookStatus newStatus) async {
    final prev = state.userBook;
    if (prev == null) return;

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
```

- [ ] **Step 2: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 3: 커밋**

```bash
git add app/lib/features/book_detail/providers/book_detail_provider.dart
git commit -m "feat: BookDetailProvider — 낙관적 업데이트 + 자동 저장"
```

---

## Task 5: 상세 화면 위젯

**Files:**
- Create: `app/lib/features/book_detail/widgets/rating_selector.dart`
- Create: `app/lib/features/book_detail/widgets/emotion_tag_chips.dart`
- Create: `app/lib/features/book_detail/widgets/review_text_section.dart`

- [ ] **Step 1: RatingSelector 위젯**

```dart
// app/lib/features/book_detail/widgets/rating_selector.dart
import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';

class RatingSelector extends StatelessWidget {
  final String? currentRating;
  final ValueChanged<String> onChanged;

  const RatingSelector({
    super.key,
    this.currentRating,
    required this.onChanged,
  });

  static const _options = [
    ('good', '좋았다', Icons.thumb_up_outlined, Icons.thumb_up),
    ('neutral', '보통', Icons.horizontal_rule, Icons.horizontal_rule),
    ('bad', '별로', Icons.thumb_down_outlined, Icons.thumb_down),
  ];

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '이 책 어때요?',
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                color: AppColors.textPrimary,
                fontWeight: FontWeight.w600,
              ),
        ),
        const SizedBox(height: 12),
        Row(
          children: _options.map((option) {
            final (value, label, iconOutlined, iconFilled) = option;
            final isSelected = currentRating == value;
            return Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: _RatingButton(
                  label: label,
                  icon: isSelected ? iconFilled : iconOutlined,
                  isSelected: isSelected,
                  onTap: () => onChanged(value),
                ),
              ),
            );
          }).toList(),
        ),
      ],
    );
  }
}

class _RatingButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool isSelected;
  final VoidCallback onTap;

  const _RatingButton({
    required this.label,
    required this.icon,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: isSelected ? AppColors.primary.withValues(alpha: 0.1) : Colors.transparent,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            border: Border.all(
              color: isSelected ? AppColors.primary : AppColors.shelf,
              width: isSelected ? 1.5 : 1,
            ),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            children: [
              Icon(
                icon,
                size: 24,
                color: isSelected ? AppColors.primary : AppColors.textSecondary,
              ),
              const SizedBox(height: 4),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12,
                  color: isSelected ? AppColors.primary : AppColors.textSecondary,
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: EmotionTagChips 위젯**

```dart
// app/lib/features/book_detail/widgets/emotion_tag_chips.dart
import 'package:flutter/material.dart';
import '../../../core/models/emotion_tag.dart';
import '../../../core/theme/app_colors.dart';

class EmotionTagChips extends StatelessWidget {
  final List<EmotionTag> options;
  final List<String> selectedIds;
  final ValueChanged<String> onToggle;

  const EmotionTagChips({
    super.key,
    required this.options,
    required this.selectedIds,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '이 책의 느낌은?',
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                color: AppColors.textPrimary,
                fontWeight: FontWeight.w600,
              ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: options.map((tag) {
            final isSelected = selectedIds.contains(tag.id);
            return FilterChip(
              label: Text(tag.label),
              selected: isSelected,
              onSelected: (_) => onToggle(tag.id),
              selectedColor: AppColors.primary.withValues(alpha: 0.15),
              checkmarkColor: AppColors.primary,
              side: BorderSide(
                color: isSelected ? AppColors.primary : AppColors.shelf,
              ),
              labelStyle: TextStyle(
                color: isSelected ? AppColors.primary : AppColors.textSecondary,
                fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                fontSize: 13,
              ),
            );
          }).toList(),
        ),
      ],
    );
  }
}
```

- [ ] **Step 3: ReviewTextSection 위젯**

```dart
// app/lib/features/book_detail/widgets/review_text_section.dart
import 'dart:math';
import 'package:flutter/material.dart';
import '../../../core/models/reflection_prompt.dart';
import '../../../core/theme/app_colors.dart';

/// 속성 칩 (UI 전용, 저장하지 않음)
const _topicChips = [
  ('character', '캐릭터'),
  ('writing_style', '문체'),
  ('plot', '전개'),
  ('atmosphere', '분위기'),
  ('message', '메시지'),
  ('worldbuilding', '세계관'),
];

/// 속성 칩별 placeholder 힌트
const _topicHints = {
  'character': '캐릭터에 대해 적어보세요... 어떤 인물이 기억에 남나요?',
  'writing_style': '문체에 대해 적어보세요... 어떤 문장이 좋았나요?',
  'plot': '전개에 대해 적어보세요... 어떤 장면이 인상적이었나요?',
  'atmosphere': '분위기에 대해 적어보세요... 어떤 느낌이었나요?',
  'message': '메시지에 대해 적어보세요... 어떤 생각이 들었나요?',
  'worldbuilding': '세계관에 대해 적어보세요... 어떤 세계가 그려졌나요?',
};

class ReviewTextSection extends StatefulWidget {
  final String? initialText;
  final List<ReflectionPrompt> prompts;
  final ValueChanged<String> onSave;

  const ReviewTextSection({
    super.key,
    this.initialText,
    required this.prompts,
    required this.onSave,
  });

  @override
  State<ReviewTextSection> createState() => _ReviewTextSectionState();
}

class _ReviewTextSectionState extends State<ReviewTextSection> {
  late final TextEditingController _controller;
  bool _helpExpanded = false;
  String? _selectedTopic;
  String _placeholder = '이 책에 대해 자유롭게 적어보세요...';
  bool _hasChanges = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText ?? '');
    _controller.addListener(() {
      final changed = _controller.text != (widget.initialText ?? '');
      if (changed != _hasChanges) {
        setState(() => _hasChanges = changed);
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onTopicTap(String category) {
    setState(() {
      _selectedTopic = _selectedTopic == category ? null : category;
      _placeholder = _selectedTopic != null
          ? _topicHints[_selectedTopic]!
          : '이 책에 대해 자유롭게 적어보세요...';
    });
  }

  void _onPromptTap(ReflectionPrompt prompt) {
    final current = _controller.text;
    final separator = current.isNotEmpty && !current.endsWith('\n') ? '\n' : '';
    _controller.text = '$current$separator${prompt.question}\n';
    _controller.selection = TextSelection.collapsed(
      offset: _controller.text.length,
    );
  }

  ReflectionPrompt _getRandomPrompt() {
    final filtered = _selectedTopic != null
        ? widget.prompts.where((p) => p.category == _selectedTopic).toList()
        : <ReflectionPrompt>[];

    // 카테고리 매칭 결과가 없으면 범용 질문(category == null)으로 폴백
    final pool = filtered.isNotEmpty
        ? filtered
        : widget.prompts.where((p) => p.category == null).toList();

    if (pool.isEmpty) return widget.prompts.first;
    return pool[Random().nextInt(pool.length)];
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 텍스트 입력
        TextField(
          controller: _controller,
          maxLines: 5,
          minLines: 3,
          decoration: InputDecoration(
            hintText: _placeholder,
            hintStyle: TextStyle(color: AppColors.textSecondary.withValues(alpha: 0.6)),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: AppColors.shelf),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: AppColors.primary, width: 1.5),
            ),
            contentPadding: const EdgeInsets.all(16),
          ),
        ),

        const SizedBox(height: 8),

        // 저장 버튼 + 도움 링크
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            // 글쓰기 도움 토글
            GestureDetector(
              onTap: () => setState(() => _helpExpanded = !_helpExpanded),
              child: Text(
                _helpExpanded ? '도움 접기' : '뭘 쓸지 모르겠다면?',
                style: TextStyle(
                  color: AppColors.primary,
                  fontSize: 13,
                ),
              ),
            ),
            // 저장 버튼
            if (_hasChanges)
              FilledButton(
                onPressed: () {
                  widget.onSave(_controller.text);
                  setState(() => _hasChanges = false);
                },
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                ),
                child: const Text('저장', style: TextStyle(fontSize: 13)),
              ),
          ],
        ),

        // 글쓰기 도움 패널 (접기/펼치기)
        if (_helpExpanded) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppColors.surfaceVariant,
              borderRadius: BorderRadius.circular(12),
              border: Border(
                left: BorderSide(color: AppColors.primary, width: 3),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 속성 칩
                Text(
                  '이런 주제로 써보세요',
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: _topicChips.map((chip) {
                    final (category, label) = chip;
                    final isSelected = _selectedTopic == category;
                    return ActionChip(
                      label: Text(label),
                      onPressed: () => _onTopicTap(category),
                      backgroundColor: isSelected
                          ? AppColors.primary.withValues(alpha: 0.15)
                          : Colors.white,
                      side: BorderSide(
                        color: isSelected ? AppColors.primary : AppColors.shelf,
                      ),
                      labelStyle: TextStyle(
                        fontSize: 12,
                        color: isSelected ? AppColors.primary : AppColors.textSecondary,
                      ),
                    );
                  }).toList(),
                ),

                const SizedBox(height: 16),

                // 리플렉션 질문
                Text(
                  '또는 질문에 답해보세요',
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 8),
                if (widget.prompts.isNotEmpty)
                  _ReflectionQuestionCard(
                    prompt: _getRandomPrompt(),
                    onTap: _onPromptTap,
                    onRefresh: () => setState(() {}),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class _ReflectionQuestionCard extends StatelessWidget {
  final ReflectionPrompt prompt;
  final ValueChanged<ReflectionPrompt> onTap;
  final VoidCallback onRefresh;

  const _ReflectionQuestionCard({
    required this.prompt,
    required this.onTap,
    required this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => onTap(prompt),
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(
                '"${prompt.question}"',
                style: TextStyle(
                  fontSize: 13,
                  color: AppColors.textPrimary,
                ),
              ),
            ),
            const SizedBox(width: 8),
            GestureDetector(
              onTap: onRefresh,
              child: Icon(
                Icons.refresh,
                size: 20,
                color: AppColors.primary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 4: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 5: 커밋**

```bash
git add app/lib/features/book_detail/widgets/
git commit -m "feat: 상세 화면 위젯 — RatingSelector, EmotionTagChips, ReviewTextSection"
```

---

## Task 6: BookDetailScreen 조립

**Files:**
- Create: `app/lib/features/book_detail/screens/book_detail_screen.dart`

- [ ] **Step 1: 화면 구현**

```dart
// app/lib/features/book_detail/screens/book_detail_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../providers/book_detail_provider.dart';
import '../widgets/rating_selector.dart';
import '../widgets/emotion_tag_chips.dart';
import '../widgets/review_text_section.dart';

class BookDetailScreen extends ConsumerWidget {
  final String userBookId;

  const BookDetailScreen({super.key, required this.userBookId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detailState = ref.watch(bookDetailProvider(userBookId));
    final emotionTagsAsync = ref.watch(emotionTagOptionsProvider);
    final promptsAsync = ref.watch(reflectionPromptsProvider);

    if (detailState.isLoading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }

    if (detailState.error != null || detailState.userBook == null) {
      return Scaffold(
        appBar: AppBar(),
        body: Center(
          child: Text('불러오기 실패: ${detailState.error ?? "알 수 없는 오류"}'),
        ),
      );
    }

    final userBook = detailState.userBook!;
    final book = userBook.book;

    return Scaffold(
      appBar: AppBar(
        title: Text(book?.title ?? ''),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. 책 정보 (컴팩트)
            _BookInfoHeader(
              userBook: userBook,
              onStatusChange: () => _showStatusBottomSheet(context, ref, userBook),
            ),

            const SizedBox(height: 28),

            // 2. 호오 평가
            RatingSelector(
              currentRating: userBook.rating,
              onChanged: (rating) async {
                try {
                  await ref
                      .read(bookDetailProvider(userBookId).notifier)
                      .updateRating(rating);
                } catch (_) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                    );
                  }
                }
              },
            ),

            const SizedBox(height: 28),

            // 3. 감성 태그
            emotionTagsAsync.when(
              data: (tags) => EmotionTagChips(
                options: tags,
                selectedIds: userBook.emotionTags ?? [],
                onToggle: (tagId) async {
                  try {
                    await ref
                        .read(bookDetailProvider(userBookId).notifier)
                        .toggleEmotionTag(tagId);
                  } catch (_) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                      );
                    }
                  }
                },
              ),
              loading: () => const SizedBox.shrink(),
              error: (_, __) => const SizedBox.shrink(),
            ),

            const SizedBox(height: 28),

            // 4. 자유 텍스트 피드백
            promptsAsync.when(
              data: (prompts) => ReviewTextSection(
                initialText: userBook.reviewText,
                prompts: prompts,
                onSave: (text) async {
                  try {
                    await ref
                        .read(bookDetailProvider(userBookId).notifier)
                        .saveReviewText(text);
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('리뷰가 저장되었습니다')),
                      );
                    }
                  } catch (_) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('저장 실패, 다시 시도해주세요')),
                      );
                    }
                  }
                },
              ),
              loading: () => const SizedBox.shrink(),
              error: (_, __) => const SizedBox.shrink(),
            ),

            const SizedBox(height: 40),
          ],
        ),
      ),
    );
  }

  void _showStatusBottomSheet(
    BuildContext context,
    WidgetRef ref,
    UserBook userBook,
  ) {
    showModalBottomSheet<BookStatus>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                '읽기 상태 변경',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
              ),
            ),
            ListTile(
              leading: Icon(
                Icons.auto_stories,
                color: userBook.status == BookStatus.reading
                    ? AppColors.primary
                    : null,
              ),
              title: const Text('읽는 중'),
              trailing: userBook.status == BookStatus.reading
                  ? const Icon(Icons.check, color: AppColors.primary)
                  : null,
              onTap: () => Navigator.pop(context, BookStatus.reading),
            ),
            ListTile(
              leading: Icon(
                Icons.check_circle_outline,
                color: userBook.status == BookStatus.read
                    ? AppColors.primary
                    : null,
              ),
              title: const Text('다 읽었어요'),
              trailing: userBook.status == BookStatus.read
                  ? const Icon(Icons.check, color: AppColors.primary)
                  : null,
              onTap: () => Navigator.pop(context, BookStatus.read),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    ).then((status) async {
      if (status == null || status == userBook.status) return;
      try {
        await ref
            .read(bookDetailProvider(userBookId).notifier)
            .updateStatus(status);
      } catch (_) {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('상태 변경 실패')),
          );
        }
      }
    });
  }
}

class _BookInfoHeader extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback onStatusChange;

  const _BookInfoHeader({
    required this.userBook,
    required this.onStatusChange,
  });

  @override
  Widget build(BuildContext context) {
    final book = userBook.book;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 표지
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: book?.coverUrl != null
              ? Image.network(
                  book!.coverUrl!,
                  width: 80,
                  height: 120,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => _coverPlaceholder(),
                )
              : _coverPlaceholder(),
        ),
        const SizedBox(width: 16),

        // 정보
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                book?.title ?? '',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary,
                    ),
              ),
              if (book?.author != null) ...[
                const SizedBox(height: 4),
                Text(
                  book!.author!,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: AppColors.textSecondary,
                      ),
                ),
              ],
              const SizedBox(height: 12),

              // 읽기 상태
              InkWell(
                onTap: onStatusChange,
                borderRadius: BorderRadius.circular(8),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppColors.primary.withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        userBook.status == BookStatus.reading
                            ? Icons.auto_stories
                            : Icons.check_circle_outline,
                        size: 16,
                        color: AppColors.primary,
                      ),
                      const SizedBox(width: 6),
                      Text(
                        userBook.status == BookStatus.reading
                            ? '읽는 중'
                            : '다 읽었어요',
                        style: TextStyle(
                          fontSize: 13,
                          color: AppColors.primary,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(width: 4),
                      Icon(
                        Icons.chevron_right,
                        size: 16,
                        color: AppColors.primary,
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _coverPlaceholder() {
    return Container(
      width: 80,
      height: 120,
      decoration: BoxDecoration(
        color: AppColors.shelf,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Icon(Icons.menu_book, color: AppColors.textSecondary),
    );
  }
}
```

- [ ] **Step 2: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 3: 커밋**

```bash
git add app/lib/features/book_detail/screens/book_detail_screen.dart
git commit -m "feat: BookDetailScreen — 책 정보 + 호오 + 감성태그 + 리뷰"
```

---

## Task 7: 라우터 + 네비게이션 연결

**Files:**
- Modify: `app/lib/routing/app_router.dart`
- Modify: `app/lib/core/services/book_registration_service.dart`
- Modify: `app/lib/features/bookshelf/providers/bookshelf_provider.dart`
- Modify: `app/lib/features/bookshelf/screens/bookshelf_screen.dart`
- Modify: `app/lib/features/search/screens/book_search_screen.dart`

- [ ] **Step 1: 라우터에 상세 화면 라우트 추가**

`app/lib/routing/app_router.dart`에 import 추가:
```dart
import '../features/book_detail/screens/book_detail_screen.dart';
```

routes 배열에 추가 (`/search` 뒤):
```dart
      GoRoute(
        path: '/book/:userBookId',
        builder: (context, state) => BookDetailScreen(
          userBookId: state.pathParameters['userBookId']!,
        ),
      ),
```

- [ ] **Step 2: BookRegistrationService.registerBook → userBookId 반환**

`app/lib/core/services/book_registration_service.dart` 수정:

기존:
```dart
  Future<void> registerBook(Book book, BookStatus status) async {
```

교체:
```dart
  /// 책 등록 파이프라인. 등록된 user_books.id를 반환.
  Future<String> registerBook(Book book, BookStatus status) async {
```

기존 (`// 2. user_books insert`):
```dart
    await _supabase.from('user_books').insert({
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
    });
```

교체:
```dart
    final userBookResult = await _supabase.from('user_books').insert({
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
    }).select('id');

    final userBookId = userBookResult.first['id'] as String;
```

기존:
```dart
    _enrichBookAsync(bookId, book);
  }
```

교체:
```dart
    _enrichBookAsync(bookId, book);
    return userBookId;
  }
```

- [ ] **Step 3: addBookToShelf → userBookId 반환**

`app/lib/features/bookshelf/providers/bookshelf_provider.dart` 수정:

기존:
```dart
Future<void> addBookToShelf(WidgetRef ref, Book book, BookStatus status) async {
  final service = ref.read(registrationServiceProvider);
  await service.registerBook(book, status);
  ref.invalidate(bookshelfProvider);
}
```

교체:
```dart
/// 서재에 책 추가. 등록된 userBookId를 반환.
Future<String> addBookToShelf(WidgetRef ref, Book book, BookStatus status) async {
  final service = ref.read(registrationServiceProvider);
  final userBookId = await service.registerBook(book, status);
  ref.invalidate(bookshelfProvider);
  return userBookId;
}
```

- [ ] **Step 4: BookshelfScreen — BookSpine 탭 시 상세 화면 이동**

`app/lib/features/bookshelf/screens/bookshelf_screen.dart` — `_buildSection` 메서드의 `BookshelfRow` 수정.

현재 `BookshelfRow`에서 `onBookTap`은 `Book` 객체를 받는데, 상세 화면 이동에는 `userBookId`가 필요하므로 `UserBook` 리스트를 전달하도록 변경.

기존 (`_buildSection`의 BookshelfRow 부분):
```dart
        BookshelfRow(
          books: booksWithData,
          onBookTap: (book) {
            // TODO: 책 상세 화면으로 이동
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(book.title)),
            );
          },
        ),
```

교체:
```dart
        BookshelfRow(
          books: booksWithData,
          onBookTap: (book) {
            // book에 매칭되는 UserBook 찾기
            final userBook = books.firstWhere(
              (ub) => ub.book?.id == book.id,
            );
            context.push('/book/${userBook.id}');
          },
        ),
```

- [ ] **Step 5: BookSearchScreen — 스낵바에 "보러가기" 추가**

`app/lib/features/search/screens/book_search_screen.dart` — import 추가:
```dart
import 'package:go_router/go_router.dart';
```

`_showStatusBottomSheet` 메서드의 try 블록 수정:

기존:
```dart
      await addBookToShelf(ref, book, status);
      ref.read(bookSearchProvider.notifier).markAsAdded(book.isbn);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${book.title} 서재에 추가됨')),
        );
      }
```

교체:
```dart
      final userBookId = await addBookToShelf(ref, book, status);
      ref.read(bookSearchProvider.notifier).markAsAdded(book.isbn);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('${book.title} 서재에 추가됨'),
            action: SnackBarAction(
              label: '보러가기',
              onPressed: () => context.push('/book/$userBookId'),
            ),
          ),
        );
      }
```

- [ ] **Step 6: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 7: 커밋**

```bash
git add app/lib/routing/app_router.dart app/lib/core/services/book_registration_service.dart app/lib/features/bookshelf/ app/lib/features/search/screens/book_search_screen.dart
git commit -m "feat: 네비게이션 연결 — 서재 탭 + 검색 스낵바 보러가기 + 라우터"
```

---

## Task 8: 테스트 + 최종 검증

**Files:**
- Create/Modify: `app/test/book_detail_test.dart`

- [ ] **Step 1: RatingSelector 위젯 테스트**

```dart
// app/test/book_detail_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/features/book_detail/widgets/rating_selector.dart';

void main() {
  group('RatingSelector', () {
    testWidgets('shows 3 rating options', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: RatingSelector(
              onChanged: (_) {},
            ),
          ),
        ),
      );

      expect(find.text('좋았다'), findsOneWidget);
      expect(find.text('보통'), findsOneWidget);
      expect(find.text('별로'), findsOneWidget);
    });

    testWidgets('calls onChanged when tapped', (tester) async {
      String? selected;

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: RatingSelector(
              onChanged: (v) => selected = v,
            ),
          ),
        ),
      );

      await tester.tap(find.text('좋았다'));
      expect(selected, 'good');
    });

    testWidgets('highlights selected rating', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: RatingSelector(
              currentRating: 'good',
              onChanged: (_) {},
            ),
          ),
        ),
      );

      // 선택된 항목의 filled 아이콘 확인
      expect(find.byIcon(Icons.thumb_up), findsOneWidget);
      expect(find.byIcon(Icons.thumb_down_outlined), findsOneWidget);
    });
  });

  group('EmotionTagChips', () {
    testWidgets('renders tags and highlights selected', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: EmotionTagChips(
              options: [
                EmotionTag(id: '1', label: '잔잔한', sortOrder: 1, isActive: true),
                EmotionTag(id: '2', label: '따뜻한', sortOrder: 2, isActive: true),
              ],
              selectedIds: ['1'],
              onToggle: (_) {},
            ),
          ),
        ),
      );

      expect(find.text('잔잔한'), findsOneWidget);
      expect(find.text('따뜻한'), findsOneWidget);
    });
  });

  group('ReviewTextSection', () {
    testWidgets('shows help panel on tap', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: ReviewTextSection(
                prompts: [],
                onSave: (_) {},
              ),
            ),
          ),
        ),
      );

      expect(find.text('뭘 쓸지 모르겠다면?'), findsOneWidget);
      expect(find.text('이런 주제로 써보세요'), findsNothing);

      await tester.tap(find.text('뭘 쓸지 모르겠다면?'));
      await tester.pump();

      expect(find.text('이런 주제로 써보세요'), findsOneWidget);
    });

    testWidgets('shows save button when text changes', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: ReviewTextSection(
                prompts: [],
                onSave: (_) {},
              ),
            ),
          ),
        ),
      );

      expect(find.text('저장'), findsNothing);

      await tester.enterText(find.byType(TextField), '좋은 책이었다');
      await tester.pump();

      expect(find.text('저장'), findsOneWidget);
    });
  });
}
```

import 추가 (파일 상단):
```dart
import 'package:curation_app/features/book_detail/widgets/emotion_tag_chips.dart';
import 'package:curation_app/features/book_detail/widgets/review_text_section.dart';
import 'package:curation_app/core/models/emotion_tag.dart';
```

- [ ] **Step 2: 전체 테스트 실행**

Run: `cd app && flutter test`
Expected: ALL PASS

- [ ] **Step 3: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 4: 커밋**

```bash
git add app/test/book_detail_test.dart
git commit -m "test: 책 상세 화면 위젯 테스트 — 호오, 감성태그, 리뷰 패널"
```

---

## 작업 순서 요약

| Task | 내용 | 의존성 |
|------|------|--------|
| 1 | DB 마이그레이션 005 | 없음 |
| 2 | UserBook 모델 확장 | 없음 |
| 3 | EmotionTag + ReflectionPrompt 모델 | 없음 |
| 4 | BookDetailProvider | Task 2, 3 |
| 5 | 상세 화면 위젯 | Task 3 |
| 6 | BookDetailScreen 조립 | Task 4, 5 |
| 7 | 라우터 + 네비게이션 연결 | Task 6 |
| 8 | 테스트 + 최종 검증 | Task 7 |
