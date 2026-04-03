// app/lib/features/feedback/screens/feedback_flow_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/book.dart';
import '../../../core/theme/app_colors.dart';
import '../providers/feedback_flow_provider.dart';

class FeedbackFlowScreen extends ConsumerStatefulWidget {
  final String userBookId;

  const FeedbackFlowScreen({super.key, required this.userBookId});

  @override
  ConsumerState<FeedbackFlowScreen> createState() => _FeedbackFlowScreenState();
}

class _FeedbackFlowScreenState extends ConsumerState<FeedbackFlowScreen> {
  late final TextEditingController _reviewController;

  @override
  void initState() {
    super.initState();
    _reviewController = TextEditingController();
    // provider가 로드되면 기존 reviewText 채움
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final reviewText = ref
          .read(feedbackFlowProvider(widget.userBookId))
          .reviewText;
      if (reviewText.isNotEmpty) {
        _reviewController.text = reviewText;
      }
    });
  }

  @override
  void dispose() {
    _reviewController.dispose();
    super.dispose();
  }

  Future<void> _handleSubmit() async {
    final notifier = ref.read(feedbackFlowProvider(widget.userBookId).notifier);
    notifier.setReviewText(_reviewController.text);

    try {
      await notifier.submit();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('피드백 감사해요'),
            duration: Duration(seconds: 2),
          ),
        );
        context.pop();
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('저장 중 오류가 발생했어요'),
            duration: Duration(seconds: 2),
          ),
        );
      }
    }
  }

  Future<void> _handleSkip() async {
    final notifier = ref.read(feedbackFlowProvider(widget.userBookId).notifier);
    notifier.setReviewText(_reviewController.text);

    try {
      await notifier.skip();
    } catch (_) {
      // skip 실패 시 무시하고 닫음
    }

    if (mounted) context.pop();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(feedbackFlowProvider(widget.userBookId));

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        child: state.isLoading
            ? const Center(child: CircularProgressIndicator())
            : state.error != null
                ? _ErrorView(onClose: () => context.pop())
                : _Content(
                    state: state,
                    userBookId: widget.userBookId,
                    reviewController: _reviewController,
                    onSubmit: _handleSubmit,
                    onSkip: _handleSkip,
                  ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 에러 뷰
// ---------------------------------------------------------------------------

class _ErrorView extends StatelessWidget {
  final VoidCallback onClose;
  const _ErrorView({required this.onClose});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _TopBar(onClose: onClose),
        const Expanded(
          child: Center(
            child: Text(
              '불러오는 중 오류가 발생했어요',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// 메인 컨텐츠
// ---------------------------------------------------------------------------

class _Content extends ConsumerWidget {
  final FeedbackFlowState state;
  final String userBookId;
  final TextEditingController reviewController;
  final VoidCallback onSubmit;
  final VoidCallback onSkip;

  const _Content({
    required this.state,
    required this.userBookId,
    required this.reviewController,
    required this.onSubmit,
    required this.onSkip,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final notifier = ref.read(feedbackFlowProvider(userBookId).notifier);
    final book = state.userBook?.book;

    return Column(
      children: [
        _TopBar(onClose: onSkip),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SizedBox(height: 24),

                // 책 정보
                if (book != null) _BookInfo(book: book),

                const SizedBox(height: 32),

                // 평가 섹션
                _SectionLabel('어떠셨어요?'),
                const SizedBox(height: 12),
                _RatingRow(
                  currentRating: state.rating,
                  onSelect: notifier.setRating,
                ),

                const SizedBox(height: 28),

                // 감성 태그 섹션
                _SectionLabel('어떤 점이 인상적이었어요?'),
                const SizedBox(height: 12),
                _EmotionTagGrid(
                  selectedTags: state.selectedTags,
                  onToggle: notifier.toggleTag,
                ),

                const SizedBox(height: 28),

                // 텍스트 섹션
                _SectionLabel('한 줄 감상 (선택)'),
                const SizedBox(height: 12),
                _ReviewTextField(controller: reviewController),

                const SizedBox(height: 32),

                // 완료 버튼
                _SubmitButton(
                  isSaving: state.isSaving,
                  onTap: onSubmit,
                ),

                const SizedBox(height: 16),

                // 나중에 할게요
                Center(
                  child: GestureDetector(
                    onTap: state.isSaving ? null : onSkip,
                    child: const Text(
                      '나중에 할게요',
                      style: TextStyle(
                        fontSize: 13,
                        color: AppColors.textSecondary,
                        decoration: TextDecoration.underline,
                        decorationColor: AppColors.textSecondary,
                      ),
                    ),
                  ),
                ),

                const SizedBox(height: 32),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// 상단 바
// ---------------------------------------------------------------------------

class _TopBar extends StatelessWidget {
  final VoidCallback onClose;
  const _TopBar({required this.onClose});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        children: [
          const Expanded(
            child: Text(
              '피드백',
              style: TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w600,
                color: AppColors.textPrimary,
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.close, color: AppColors.textPrimary),
            onPressed: onClose,
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 책 정보
// ---------------------------------------------------------------------------

class _BookInfo extends StatelessWidget {
  final Book? book;

  const _BookInfo({required this.book});

  @override
  Widget build(BuildContext context) {
    if (book == null) return const SizedBox.shrink();
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 커버 이미지
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: book!.coverUrl != null && book!.coverUrl!.isNotEmpty
              ? Image.network(
                  book!.coverUrl!,
                  width: 72,
                  height: 104,
                  fit: BoxFit.cover,
                  errorBuilder: (context2, error, stackTrace) => _CoverPlaceholder(title: book!.title),
                )
              : _CoverPlaceholder(title: book!.title),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                book!.title,
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w500,
                  color: AppColors.textPrimary,
                ),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 4),
              Text(
                book!.author ?? '',
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w300,
                  color: AppColors.textSecondary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _CoverPlaceholder extends StatelessWidget {
  final String title;
  const _CoverPlaceholder({required this.title});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 72,
      height: 104,
      decoration: BoxDecoration(
        color: AppColors.spineColorFromTitle(title),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(6),
          child: Text(
            title,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 10,
              fontWeight: FontWeight.w500,
            ),
            textAlign: TextAlign.center,
            maxLines: 4,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 섹션 레이블
// ---------------------------------------------------------------------------

class _SectionLabel extends StatelessWidget {
  final String text;
  const _SectionLabel(this.text);

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w500,
        color: AppColors.textPrimary,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 평가 버튼 행
// ---------------------------------------------------------------------------

class _RatingRow extends StatelessWidget {
  final String? currentRating;
  final ValueChanged<String> onSelect;

  const _RatingRow({required this.currentRating, required this.onSelect});

  static const _options = [
    ('good', '좋았어요', '👍'),
    ('neutral', '보통이에요', '🤔'),
    ('bad', '별로였어요', '👎'),
  ];

  @override
  Widget build(BuildContext context) {
    return Row(
      children: _options.map((opt) {
        final (value, label, emoji) = opt;
        final isSelected = currentRating == value;
        return Expanded(
          child: Padding(
            padding: EdgeInsets.only(
              right: value != 'bad' ? 8 : 0,
            ),
            child: _RatingButton(
              emoji: emoji,
              label: label,
              isSelected: isSelected,
              onTap: () => onSelect(value),
            ),
          ),
        );
      }).toList(),
    );
  }
}

class _RatingButton extends StatelessWidget {
  final String emoji;
  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  const _RatingButton({
    required this.emoji,
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(vertical: 14),
        decoration: BoxDecoration(
          color: isSelected ? AppColors.primary : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isSelected ? AppColors.primary : const Color(0xFFF1F5F9),
            width: 1.5,
          ),
        ),
        child: Column(
          children: [
            Text(emoji, style: const TextStyle(fontSize: 18)),
            const SizedBox(height: 4),
            Text(
              label,
              style: TextStyle(
                fontSize: 12,
                color: isSelected ? Colors.white : AppColors.textSecondary,
                fontWeight:
                    isSelected ? FontWeight.w600 : FontWeight.normal,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 감성 태그 그리드
// ---------------------------------------------------------------------------

class _EmotionTagGrid extends StatelessWidget {
  final List<String> selectedTags;
  final ValueChanged<String> onToggle;

  const _EmotionTagGrid({
    required this.selectedTags,
    required this.onToggle,
  });

  static const _tags = ['캐릭터', '문체', '세계관', '스토리', '메시지', '분위기'];

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: _tags.map((tag) {
        final isSelected = selectedTags.contains(tag);
        return GestureDetector(
          onTap: () => onToggle(tag),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            decoration: BoxDecoration(
              color: isSelected ? AppColors.primary : Colors.transparent,
              borderRadius: BorderRadius.circular(20),
              border: Border.all(
                color: isSelected ? AppColors.primary : const Color(0xFFF1F5F9),
                width: 1.5,
              ),
            ),
            child: Text(
              tag,
              style: TextStyle(
                fontSize: 13,
                color: isSelected ? Colors.white : AppColors.textSecondary,
                fontWeight:
                    isSelected ? FontWeight.w500 : FontWeight.normal,
              ),
            ),
          ),
        );
      }).toList(),
    );
  }
}

// ---------------------------------------------------------------------------
// 리뷰 텍스트 필드
// ---------------------------------------------------------------------------

class _ReviewTextField extends StatelessWidget {
  final TextEditingController controller;

  const _ReviewTextField({required this.controller});

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      maxLines: 3,
      style: const TextStyle(
        fontSize: 14,
        color: AppColors.textPrimary,
      ),
      decoration: InputDecoration(
        hintText: '자유롭게 적어주세요',
        hintStyle: const TextStyle(
          color: AppColors.textSecondary,
          fontSize: 14,
        ),
        filled: true,
        fillColor: const Color(0xFFFAFAFA),
        contentPadding: const EdgeInsets.all(16),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(
            color: Color(0xFFF1F5F9),
            width: 1.5,
          ),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(
            color: AppColors.primary,
            width: 1.5,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 완료 버튼
// ---------------------------------------------------------------------------

class _SubmitButton extends StatelessWidget {
  final bool isSaving;
  final VoidCallback onTap;

  const _SubmitButton({required this.isSaving, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: GestureDetector(
        onTap: isSaving ? null : onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(vertical: 16),
          decoration: BoxDecoration(
            color:
                isSaving ? AppColors.primaryLight : AppColors.primary,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Center(
            child: isSaving
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Text(
                    '완료',
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                      color: Colors.white,
                    ),
                  ),
          ),
        ),
      ),
    );
  }
}
