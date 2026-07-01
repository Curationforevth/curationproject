import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/recommendation_service.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../onboarding/providers/onboarding_provider.dart';
import '../../onboarding/screens/onboarding_screen.dart';
import '../providers/home_provider.dart';
import '../providers/recommendation_provider.dart';
import '../widgets/book_detail_bottom_sheet.dart';
import '../widgets/my_books_section.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bookshelfAsync = ref.watch(bookshelfProvider);

    return Scaffold(
      backgroundColor: AppColors.surface,
      body: SafeArea(
        // 서재가 비어있고 아직 온보딩을 마치거나 건너뛰지 않았으면 온보딩을 띄운다.
        // 그 외엔 _HomeContent — 0권이어도(온보딩 건너뜀) 서버 /home(트렌딩+큐레이션)
        // 콜드스타트 피드를 보여줘 "죽은 빈 홈"을 없앤다.
        child: bookshelfAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => Center(child: Text('오류: $e')),
          data: (books) {
            if (books.isEmpty && !ref.watch(onboardingDismissedProvider)) {
              return const OnboardingScreen();
            }
            return _HomeContent();
          },
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Main content (server feed; renders at 0 books too)
// ---------------------------------------------------------------------------

class _HomeContent extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wishlist = ref.watch(wishlistBooksProvider);
    final wishlistWithBook =
        wishlist.where((ub) => ub.book != null).toList();

    return RefreshIndicator(
      // 세션 중엔 자동 리뉴얼하지 않고, 당겨서 새로고침할 때만 추천/홈피드를 갱신한다.
      // 서재(bookshelfProvider)는 무효화하지 않는다 — 그러면 그걸 watch 하는 홈 전체가
      // 로딩으로 무너져 새로고침이 안 먹는 것처럼 보였다(서재는 추가/피드백 시 자체 갱신).
      onRefresh: () async {
        ref.invalidate(homeFeedProvider);
        ref.invalidate(recommendationsProvider);
        try {
          await ref.read(homeFeedProvider.future);
        } catch (_) {}
        try {
          await ref.read(recommendationsProvider.future);
        } catch (_) {}
      },
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
        // ── Top bar ──────────────────────────────────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 0),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              // 아바타
              GestureDetector(
                onTap: () => context.push('/taste'),
                child: Container(
                  width: 30,
                  height: 30,
                  decoration: const BoxDecoration(
                    color: Color(0xFFF1F5F9),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    Icons.person,
                    size: 18,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
              // 검색 아이콘
              GestureDetector(
                onTap: () => context.push('/search'),
                child: const Icon(
                  Icons.search,
                  size: 20,
                  color: Color(0xFF94A3B8),
                ),
              ),
            ],
          ),
        ),

        // ── 헤더 텍스트 ─────────────────────────────────────────────
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
          child: Text(
            '오늘은 어떤 책을\n만나볼까요?',
            style: const TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.w300,
              color: Color(0xFF0F172A),
              letterSpacing: -1.0,
              height: 1.35,
            ),
          ),
        ),

        // ── Section 1: 내 책 ─────────────────────────────────────────
        const MyBooksSection(),

        // ── Section 2: 읽고싶은 책 ─────────────────────────────────
        if (wishlistWithBook.isNotEmpty) ...[
          const SizedBox(height: 8),
          _WishlistSection(books: wishlistWithBook),
        ],

        // ── Section 3: 이 책은 어때요? (추천) ──────────────────────
        const _RecommendationSection(),

        // ── Section 4: 큐레이션 + 화제의 책 (/home) ─────────────────
        const _CurationSections(),

        const SizedBox(height: 32),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Section 4: 큐레이션 / 화제의 책 (서버 /home 의 curation·trending 섹션)
// ---------------------------------------------------------------------------

class _CurationSections extends ConsumerWidget {
  const _CurationSections();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feedAsync = ref.watch(homeFeedProvider);
    return feedAsync.when(
      // 로딩/에러 시엔 조용히 비움 — 위 추천 섹션이 이미 화면을 채운다.
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (feed) {
        final sections = feed.sections
            .where((s) =>
                (s.type == 'curation' || s.type == 'trending') &&
                s.books.isNotEmpty)
            .toList();
        if (sections.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [for (final s in sections) _FeedRow(section: s)],
        );
      },
    );
  }
}

class _FeedRow extends StatelessWidget {
  final HomeSection section;

  const _FeedRow({required this.section});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 24, 0, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(right: 20),
            child: Text(
              section.title.isEmpty ? '추천' : section.title,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: AppColors.textPrimary,
                letterSpacing: 0.01,
              ),
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            height: 212,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.only(right: 20),
              itemCount: section.books.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final b = section.books[index];
                return _FeedBookCard(
                  book: b,
                  onTap: () =>
                      BookDetailBottomSheet.show(context, b.toBook()),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _FeedBookCard extends StatelessWidget {
  final HomeBook book;
  final VoidCallback onTap;

  const _FeedBookCard({required this.book, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 120,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: SizedBox(
                width: 120,
                height: 172,
                child: book.coverUrl != null
                    ? Image.network(
                        book.coverUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _CoverFallback(),
                      )
                    : _CoverFallback(),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            Text(
              book.author,
              style: const TextStyle(
                fontSize: 11,
                color: AppColors.textSecondary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Section 3: 이 책은 어때요? (추천)
// ---------------------------------------------------------------------------

class _RecommendationSection extends ConsumerWidget {
  const _RecommendationSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final recommendationsAsync = ref.watch(recommendationsProvider);

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 24, 20, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            '이 책은 어때요?',
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w700,
              color: AppColors.textPrimary,
              letterSpacing: 0.01,
            ),
          ),
          const SizedBox(height: 10),
          recommendationsAsync.when(
            loading: () => const SizedBox(
              height: 212,
              child: Center(
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            error: (_, __) => _RecommendationPlaceholder(),
            data: (result) {
              // 추천이 있으면 무조건 보여준다. 갓 온보딩한 유저는 feedback_embedding 이
              // 없어 has_feedback=false 지만 취향벡터로 계산된 추천은 존재한다 — 이를
              // has_feedback 으로 가리던 버그를 제거(온보딩 직후 추천이 안 보이던 원인).
              if (result.recommendations.isEmpty) {
                return _RecommendationPlaceholder(
                  message: result.computing
                      ? '맞춤 추천을 준비하고 있어요…'
                      : (result.totalLiked >= 6
                          ? '아직 추천할 책이 없어요'
                          : '읽은 책을 더 추가하면 맞춤 추천이 시작돼요'),
                );
              }
              return SizedBox(
                height: 212,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: result.recommendations.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 12),
                  itemBuilder: (context, index) {
                    final book = result.recommendations[index];
                    return _RecommendationCard(
                      book: book,
                      onTap: () => BookDetailBottomSheet.show(
                        context,
                        book.toBook(),
                      ),
                    );
                  },
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _RecommendationPlaceholder extends StatelessWidget {
  final String message;

  const _RecommendationPlaceholder({
    this.message = '피드백을 더 남기면 맞춤 추천이 시작돼요',
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: const Color(0xFFFAFAFA),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Center(
        child: Text(
          message,
          style: const TextStyle(
            fontSize: 13,
            color: Color(0xFF94A3B8),
          ),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}

class _RecommendationCard extends StatelessWidget {
  final RecommendedBook book;
  final VoidCallback onTap;

  const _RecommendationCard({required this.book, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 120,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: SizedBox(
                width: 120,
                height: 172,
                child: book.coverUrl != null
                    ? Image.network(
                        book.coverUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _CoverFallback(),
                      )
                    : _CoverFallback(),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            Text(
              book.author,
              style: const TextStyle(
                fontSize: 11,
                color: AppColors.textSecondary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }
}

class _CoverFallback extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.shelf,
      child: const Icon(
        Icons.menu_book,
        color: AppColors.textSecondary,
        size: 28,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Section 2: 읽고싶은 책
// ---------------------------------------------------------------------------

class _WishlistSection extends StatelessWidget {
  final List<UserBook> books;

  const _WishlistSection({required this.books});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 섹션 헤더
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text(
                  '읽고싶은 책',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: AppColors.textPrimary,
                    letterSpacing: 0.01,
                  ),
                ),
                Text(
                  '${books.length}권',
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          // 가로 스크롤
          SizedBox(
            height: 212,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 20),
              itemCount: books.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final ub = books[index];
                return _WishlistCard(
                  userBook: ub,
                  onTap: () => BookDetailBottomSheet.show(context, ub.book!),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _WishlistCard extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback onTap;

  const _WishlistCard({required this.userBook, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final book = userBook.book!;
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 120,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: SizedBox(
                width: 120,
                height: 172,
                child: book.coverUrl != null
                    ? Image.network(
                        book.coverUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => Container(
                          color: AppColors.shelf,
                          child: const Icon(
                            Icons.menu_book,
                            color: AppColors.textSecondary,
                            size: 28,
                          ),
                        ),
                      )
                    : Container(
                        color: AppColors.shelf,
                        child: const Icon(
                          Icons.menu_book,
                          color: AppColors.textSecondary,
                          size: 28,
                        ),
                      ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            if (book.author != null && book.author!.isNotEmpty)
              Text(
                book.author!,
                style: const TextStyle(
                  fontSize: 11,
                  color: AppColors.textSecondary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
          ],
        ),
      ),
    );
  }
}
