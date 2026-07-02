import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../home/providers/recommendation_provider.dart';
import '../providers/onboarding_provider.dart';

/// 온보딩 플로우 (서재 비어있고 미dismiss일 때 HomeScreen 이 렌더).
/// Welcome → 책 그리드 선택(5권 이상) → 최애+감성태그 → 완료 연출 → 서재.
/// 그리드는 5권 이상부터 진행 가능(PRODUCT_PLAN §4-1). 설계: docs/.../2026-03-26-onboarding-design.md
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  static const int target = 6; // /recommend Tier2 임계와 정렬
  static const int minRequired = 5; // PRODUCT_PLAN §4-1 "5권 이상 선택 시"
  static const List<String> _emotionOptions = [
    '잔잔한', '따뜻한', '긴장감', '몰입', '여운',
    '유쾌한', '무거운', '서정적', '속도감', '생각할거리',
  ];

  int _step = 0;
  final Map<String, Book> _selected = {}; // bookId → Book
  String? _favoriteId;
  final Set<String> _emotionTags = {};
  bool _submitting = false;
  bool _done = false; // 저장 성공 → "서재가 시작됐어요" 완료 연출 표시 중

  void _toggle(Book b) {
    setState(() {
      if (_selected.containsKey(b.id)) {
        _selected.remove(b.id);
        if (_favoriteId == b.id) _favoriteId = null;
      } else {
        _selected[b.id] = b;
      }
    });
  }

  Future<void> _finishWithWrite() async {
    setState(() => _submitting = true);
    try {
      await ref.read(onboardingServiceProvider).completeOnboarding(
            selectedBookIds: _selected.keys.toList(),
            favoriteBookId: _favoriteId,
            favoriteEmotionTags: _emotionTags.toList(),
          );
      // 서재가 채워졌으니 서버가 추천을 선제 재계산하게 fire-and-forget 트리거
      // (등록/피드백 경로와 동일 패턴, bookshelf_provider.dart:98 참고) →
      // 유저가 홈에 도달할 즈음엔 캐시가 warm 이도록. await 하지 않는다.
      unawaited(ref.read(recommendationServiceProvider).triggerRecompute());
      // 저장 성공 → 바로 dismiss 하지 않고 "서재가 시작됐어요" 완료 연출을 먼저 보여준다
      // (PRODUCT_PLAN §4-1). dismiss 는 완료 화면의 [시작하기] 탭에서 실행.
      ref.invalidate(bookshelfProvider);
      if (mounted) {
        setState(() {
          _submitting = false;
          _done = true;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _submitting = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('서재 저장 중 오류가 났어요: $e')),
        );
      }
    }
  }

  /// dismiss → HomeScreen 이 _HomeContent 로 전환. (서재 갱신은 저장 성공 시점에 이미 완료.)
  void _dismiss() {
    ref.read(onboardingDismissedProvider.notifier).state = true;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.milestone0,
      child: AnimatedSwitcher(
        duration: const Duration(milliseconds: 250),
        child: _submitting
            ? const _Finishing()
            : _done
                ? _buildDone()
                : switch (_step) {
                    0 => _buildWelcome(),
                    1 => _buildGrid(),
                    _ => _buildFavorite(),
                  },
      ),
    );
  }

  // ── Step 0: Welcome ──────────────────────────────────────────────────────
  Widget _buildWelcome() {
    return Padding(
      key: const ValueKey('welcome'),
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('📚', style: TextStyle(fontSize: 40)),
          const SizedBox(height: 24),
          const Text(
            '당신만의 서재를\n만들어볼까요?',
            style: TextStyle(
              fontSize: 26,
              height: 1.3,
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
          const SizedBox(height: 12),
          const Text(
            '읽은 책 몇 권만 골라주세요.\n취향에 맞는 책을 찾아드릴게요.',
            style: TextStyle(
              fontSize: 15,
              height: 1.5,
              fontWeight: FontWeight.w300,
              color: AppColors.primaryLight,
            ),
          ),
          const SizedBox(height: 40),
          _PrimaryButton(label: '시작하기', onTap: () => setState(() => _step = 1)),
          const SizedBox(height: 12),
          Center(
            child: TextButton(
              onPressed: _dismiss,
              child: const Text('나중에 할게요',
                  style: TextStyle(color: AppColors.textSecondary)),
            ),
          ),
        ],
      ),
    );
  }

  // ── Step 1: 책 그리드 선택 ────────────────────────────────────────────────
  Widget _buildGrid() {
    final poolAsync = ref.watch(onboardingPoolProvider);
    final count = _selected.length;
    return Column(
      key: const ValueKey('grid'),
      children: [
        const SizedBox(height: 16),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('읽은 책을 골라주세요',
                  style: TextStyle(
                      fontSize: 20,
                      fontWeight: FontWeight.w600,
                      color: AppColors.textPrimary)),
              const SizedBox(height: 4),
              Text('$count / $target권',
                  style: TextStyle(
                      fontSize: 13,
                      color: count >= target
                          ? AppColors.success
                          : AppColors.textSecondary)),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: (count / target).clamp(0.0, 1.0),
                  minHeight: 4,
                  backgroundColor: AppColors.shelf,
                  valueColor:
                      const AlwaysStoppedAnimation(AppColors.primary),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: poolAsync.when(
            loading: () =>
                const Center(child: CircularProgressIndicator()),
            error: (e, _) => Center(
                child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text('책을 불러오지 못했어요: $e',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: AppColors.textSecondary)),
            )),
            data: (books) => GridView.builder(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
              gridDelegate:
                  const SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: 3,
                childAspectRatio: 0.62,
                crossAxisSpacing: 12,
                mainAxisSpacing: 16,
              ),
              itemCount: books.length,
              itemBuilder: (context, i) {
                final b = books[i];
                return _CoverTile(
                  book: b,
                  selected: _selected.containsKey(b.id),
                  onTap: () => _toggle(b),
                );
              },
            ),
          ),
        ),
        _FooterBar(
          onSkip: _dismiss,
          // PRODUCT_PLAN §4-1 "5권 이상 선택 시" — 5권 미만이면 진행 불가.
          // 미달일 땐 남은 권수를 라벨에 노출해 "무엇을 더 하면 되는지"를 명확히 한다.
          primaryLabel: count >= minRequired
              ? '다음'
              : count == 0
                  ? '읽은 책을 5권 이상 골라주세요'
                  : '${minRequired - count}권만 더 골라주세요',
          primaryEnabled: count >= minRequired,
          onPrimary: () => setState(() {
            _favoriteId ??= _selected.keys.first;
            _step = 2;
          }),
        ),
      ],
    );
  }

  // ── Step 2: 최애 + 감성태그 ───────────────────────────────────────────────
  Widget _buildFavorite() {
    final selected = _selected.values.toList();
    return Column(
      key: const ValueKey('favorite'),
      children: [
        const SizedBox(height: 24),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 20),
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text('제일 좋았던 책은?',
                style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w600,
                    color: AppColors.textPrimary)),
          ),
        ),
        const SizedBox(height: 16),
        SizedBox(
          height: 150,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 20),
            itemCount: selected.length,
            separatorBuilder: (_, __) => const SizedBox(width: 12),
            itemBuilder: (context, i) {
              final b = selected[i];
              return GestureDetector(
                onTap: () => setState(() => _favoriteId = b.id),
                child: _CoverThumb(
                  book: b,
                  highlighted: _favoriteId == b.id,
                ),
              );
            },
          ),
        ),
        const SizedBox(height: 24),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 20),
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text('어떤 점이 좋았나요?',
                style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                    color: AppColors.textPrimary)),
          ),
        ),
        const SizedBox(height: 12),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _emotionOptions.map((tag) {
                final on = _emotionTags.contains(tag);
                return GestureDetector(
                  onTap: () => setState(() {
                    if (on) {
                      _emotionTags.remove(tag);
                    } else {
                      _emotionTags.add(tag);
                    }
                  }),
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 9),
                    decoration: BoxDecoration(
                      color: on ? AppColors.primary : AppColors.surface,
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                          color: on ? AppColors.primary : AppColors.shelf),
                    ),
                    child: Text(tag,
                        style: TextStyle(
                            fontSize: 14,
                            color: on
                                ? AppColors.textOnPrimary
                                : AppColors.textPrimary)),
                  ),
                );
              }).toList(),
            ),
          ),
        ),
        _FooterBar(
          onSkip: _finishWithWrite, // 감성태그 없이도 완료(선택 책은 저장)
          primaryLabel: '완료',
          primaryEnabled: true,
          onPrimary: _finishWithWrite,
        ),
      ],
    );
  }

  // ── 완료 연출: "서재가 시작됐어요" (PRODUCT_PLAN §4-1) ──────────────────────
  Widget _buildDone() {
    return _DoneCelebration(key: const ValueKey('done'), onStart: _dismiss);
  }
}

// ── 작은 위젯들 ──────────────────────────────────────────────────────────────

class _Finishing extends StatelessWidget {
  const _Finishing();
  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 20),
          Text('서재를 만들고 있어요…',
              style: TextStyle(color: AppColors.primaryLight)),
        ],
      ),
    );
  }
}

/// 저장 성공 후 노출되는 완료 연출. AnimatedScale/Opacity 만으로 가벼운 등장
/// 애니메이션을 준다(패키지 추가 없음, PRODUCT_PLAN §4-1 "당신의 서재가 시작됐어요").
class _DoneCelebration extends StatefulWidget {
  final VoidCallback onStart;
  const _DoneCelebration({super.key, required this.onStart});

  @override
  State<_DoneCelebration> createState() => _DoneCelebrationState();
}

class _DoneCelebrationState extends State<_DoneCelebration> {
  bool _visible = false;

  @override
  void initState() {
    super.initState();
    // 첫 프레임 이후 트리거 — build 중 setState 방지 + 등장감 연출.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() => _visible = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          AnimatedScale(
            scale: _visible ? 1.0 : 0.8,
            duration: const Duration(milliseconds: 400),
            curve: Curves.easeOutBack,
            child: AnimatedOpacity(
              opacity: _visible ? 1.0 : 0.0,
              duration: const Duration(milliseconds: 400),
              child: const Text('📚', style: TextStyle(fontSize: 56)),
            ),
          ),
          const SizedBox(height: 24),
          AnimatedOpacity(
            opacity: _visible ? 1.0 : 0.0,
            duration: const Duration(milliseconds: 400),
            child: const Text(
              '당신의 서재가 시작됐어요',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 24,
                height: 1.3,
                fontWeight: FontWeight.w600,
                color: AppColors.textPrimary,
              ),
            ),
          ),
          const SizedBox(height: 12),
          AnimatedOpacity(
            opacity: _visible ? 1.0 : 0.0,
            duration: const Duration(milliseconds: 400),
            child: const Text(
              '고른 책들이 서재에 꽂혔어요.\n평가를 남기면 맞춤 추천이 시작돼요',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 15,
                height: 1.5,
                fontWeight: FontWeight.w300,
                color: AppColors.primaryLight,
              ),
            ),
          ),
          const SizedBox(height: 40),
          _PrimaryButton(label: '시작하기', onTap: widget.onStart),
        ],
      ),
    );
  }
}

class _CoverTile extends StatelessWidget {
  final Book book;
  final bool selected;
  final VoidCallback onTap;
  const _CoverTile(
      {required this.book, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: _coverImage(book.coverUrl),
                ),
                if (selected)
                  Container(
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: AppColors.primary, width: 3),
                      color: AppColors.primary.withValues(alpha: 0.18),
                    ),
                    child: const Align(
                      alignment: Alignment.topRight,
                      child: Padding(
                        padding: EdgeInsets.all(4),
                        child: CircleAvatar(
                          radius: 11,
                          backgroundColor: AppColors.primary,
                          child: Icon(Icons.check,
                              size: 14, color: AppColors.textOnPrimary),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          Text(book.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                  fontSize: 11, color: AppColors.textPrimary)),
        ],
      ),
    );
  }
}

class _CoverThumb extends StatelessWidget {
  final Book book;
  final bool highlighted;
  const _CoverThumb({required this.book, required this.highlighted});

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 150),
      width: 96,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: highlighted ? AppColors.primary : Colors.transparent,
          width: 3,
        ),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: _coverImage(book.coverUrl),
      ),
    );
  }
}

Widget _coverImage(String? url) {
  if (url == null || url.isEmpty) {
    return Container(color: AppColors.shelf);
  }
  return Image.network(
    url,
    fit: BoxFit.cover,
    errorBuilder: (_, __, ___) => Container(color: AppColors.shelf),
  );
}

class _PrimaryButton extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _PrimaryButton({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 15),
          decoration: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(label,
              textAlign: TextAlign.center,
              style: const TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w500,
                  color: AppColors.textOnPrimary)),
        ),
      ),
    );
  }
}

class _FooterBar extends StatelessWidget {
  final VoidCallback onSkip;
  final String primaryLabel;
  final bool primaryEnabled;
  final VoidCallback onPrimary;
  const _FooterBar({
    required this.onSkip,
    required this.primaryLabel,
    required this.primaryEnabled,
    required this.onPrimary,
  });

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
        child: Row(
          children: [
            TextButton(
              onPressed: onSkip,
              child: const Text('건너뛰기',
                  style: TextStyle(color: AppColors.textSecondary)),
            ),
            const Spacer(),
            GestureDetector(
              onTap: primaryEnabled ? onPrimary : null,
              child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 32, vertical: 13),
                decoration: BoxDecoration(
                  color: primaryEnabled
                      ? AppColors.primary
                      : AppColors.shelf,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(primaryLabel,
                    style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w500,
                        color: primaryEnabled
                            ? AppColors.textOnPrimary
                            : AppColors.textSecondary)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
