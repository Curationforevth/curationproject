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
    final readingBooks = byStatus[BookStatus.reading] ?? [];
    final readBooks = byStatus[BookStatus.read] ?? [];

    // 작가 그룹에 포함된 책 ID (중복 제거용)
    final groupedIds = authorGroups.values
        .expand((list) => list)
        .map((ub) => ub.id)
        .toSet();

    // 읽은 책 중 작가 그룹에 안 들어간 것만
    final ungroupedReadBooks = readBooks
        .where((ub) => ub.book != null && !groupedIds.contains(ub.id))
        .toList();

    // 읽는 중 피처드 제외 나머지
    final otherReading = readingBooks
        .where((ub) => ub.book != null && ub.id != featured?.id)
        .toList();

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
        ],

        // 1-1. 읽는 중 나머지 (2권+ 시)
        if (otherReading.isNotEmpty)
          CoverFeedSection(
            title: '함께 읽는 중',
            userBooks: otherReading,
            onBookTap: (ub) => context.push('/book/${ub.id}'),
          ),

        if (featured != null || otherReading.isNotEmpty) _divider(),

        // 2. 피드백 유도 CTA
        if (unreviewed.isNotEmpty) ...[
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

        // 4. 읽은 책 (작가 그룹에 안 들어간 책만)
        if (ungroupedReadBooks.isNotEmpty)
          CoverFeedSection(
            title: '읽은 책',
            userBooks: ungroupedReadBooks,
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
