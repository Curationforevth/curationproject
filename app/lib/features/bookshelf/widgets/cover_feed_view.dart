import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/user_book.dart';
import '../../../core/theme/app_colors.dart';
import '../providers/bookshelf_provider.dart';
import 'cover_feed_section.dart';
import 'featured_reading_card.dart';
import 'feedback_cta_row.dart';

/// 커버 피드 뷰 — preview-a 목업 기반
class CoverFeedView extends ConsumerWidget {
  const CoverFeedView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final byStatus = ref.watch(booksByStatusProvider);
    final featured = ref.watch(featuredReadingProvider);
    final unreviewed = ref.watch(unreviewedBooksProvider);
    final authorGroups = ref.watch(authorGroupsProvider);
    final readBooks = byStatus[BookStatus.read] ?? [];

    return ListView(
      padding: const EdgeInsets.only(bottom: 96),
      children: [
        // 1. 읽는 중 — 피처드 카드
        if (featured != null) ...[
          const SizedBox(height: 16),
          _sectionHeader(context, '읽는 중'),
          FeaturedReadingCard(
            userBook: featured,
            onTap: () => context.push('/book/${featured.id}'),
          ),
          _divider(),
        ],

        // 2. 피드백 유도 CTA
        if (unreviewed.isNotEmpty) ...[
          const SizedBox(height: 16),
          FeedbackCtaRow(
            unreviewedBooks: unreviewed,
            onTap: () {
              final first = unreviewed.first;
              context.push('/book/${first.id}');
            },
          ),
          _divider(),
        ],

        // 3. 작가별 섹션
        ...authorGroups.entries.map((entry) => Column(
              children: [
                CoverFeedSection(
                  title: '${entry.key}의 책들',
                  userBooks: entry.value,
                  onBookTap: (ub) => context.push('/book/${ub.id}'),
                ),
                _divider(),
              ],
            )),

        // 4. 읽은 책 (작가 그룹에 안 들어간 책 포함)
        if (readBooks.where((ub) => ub.book != null).isNotEmpty)
          CoverFeedSection(
            title: '읽은 책',
            userBooks: readBooks,
            onBookTap: (ub) => context.push('/book/${ub.id}'),
          ),
      ],
    );
  }

  Widget _sectionHeader(BuildContext context, String title) {
    return Padding(
      padding: const EdgeInsets.only(left: 20, bottom: 10),
      child: Text(
        title,
        style: const TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w700,
          color: AppColors.textPrimary,
        ),
      ),
    );
  }

  Widget _divider() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
      child: Divider(height: 1, color: AppColors.textSecondary.withValues(alpha: 0.08)),
    );
  }
}
