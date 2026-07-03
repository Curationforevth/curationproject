import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/models/book.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../../home/widgets/book_detail_bottom_sheet.dart';
import '../../search/providers/book_search_provider.dart';
import '../../search/utils/resolve_sheet_book.dart';
import '../../../core/utils/author_format.dart';

class RegisterFlowScreen extends ConsumerStatefulWidget {
  const RegisterFlowScreen({super.key});

  @override
  ConsumerState<RegisterFlowScreen> createState() => _RegisterFlowScreenState();
}

class _RegisterFlowScreenState extends ConsumerState<RegisterFlowScreen> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();

  @override
  void initState() {
    super.initState();
    // Clear previous search state when screen opens
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(bookSearchProvider.notifier).clear();
      _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onSearchChanged(String query) {
    ref.read(bookSearchProvider.notifier).search(query);
  }

  void _clearSearch() {
    _controller.clear();
    ref.read(bookSearchProvider.notifier).clear();
    _focusNode.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    final searchState = ref.watch(bookSearchProvider);
    final shelfIsbns = ref.watch(bookshelfProvider).valueOrNull
            ?.map((ub) => ub.book?.isbn)
            .whereType<String>()
            .toSet() ??
        const <String>{};

    return Scaffold(
      backgroundColor: AppColors.surface,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        elevation: 0,
        scrolledUnderElevation: 0,
        automaticallyImplyLeading: false,
        title: const Text(
          '책 등록',
          style: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.w500,
            color: AppColors.textPrimary,
          ),
        ),
        centerTitle: false,
        actions: [
          IconButton(
            icon: const Icon(Icons.close, color: AppColors.textPrimary),
            onPressed: () => context.pop(),
          ),
        ],
      ),
      body: Column(
        children: [
          // Search input
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            child: Container(
              decoration: BoxDecoration(
                color: AppColors.surfaceVariant,
                borderRadius: BorderRadius.circular(12),
              ),
              child: TextField(
                controller: _controller,
                focusNode: _focusNode,
                decoration: InputDecoration(
                  hintText: '책 제목 또는 저자 검색',
                  hintStyle: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 15,
                  ),
                  prefixIcon: const Icon(
                    Icons.search,
                    color: AppColors.textSecondary,
                    size: 20,
                  ),
                  suffixIcon: _controller.text.isNotEmpty
                      ? IconButton(
                          icon: const Icon(
                            Icons.clear,
                            color: AppColors.textSecondary,
                            size: 18,
                          ),
                          onPressed: _clearSearch,
                        )
                      : null,
                  border: InputBorder.none,
                  contentPadding: const EdgeInsets.symmetric(
                    vertical: 14,
                    horizontal: 4,
                  ),
                ),
                onChanged: (query) {
                  setState(() {}); // rebuild to show/hide clear button
                  _onSearchChanged(query);
                },
                textInputAction: TextInputAction.search,
              ),
            ),
          ),

          // Results area
          Expanded(
            child: _buildResultsBody(searchState, shelfIsbns),
          ),
        ],
      ),
    );
  }

  Widget _buildResultsBody(
      BookSearchState searchState, Set<String> shelfIsbns) {
    switch (searchState.status) {
      case BookSearchStatus.idle:
        return const Center(
          child: Text(
            '책을 검색해보세요',
            style: TextStyle(
              color: AppColors.textSecondary,
              fontSize: 15,
            ),
          ),
        );
      case BookSearchStatus.loading:
        return const Center(child: CircularProgressIndicator());
      case BookSearchStatus.error:
        return Center(
          child: Text(
            '검색 실패: ${searchState.errorMessage}',
            style: const TextStyle(color: AppColors.textSecondary),
          ),
        );
      case BookSearchStatus.loaded:
        if (searchState.results.isEmpty) {
          return const Center(
            child: Text(
              '검색 결과가 없습니다',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          );
        }
        return ListView.builder(
          itemCount:
              searchState.results.length + (searchState.hasMore ? 1 : 0),
          itemBuilder: (context, index) {
            if (index == searchState.results.length) {
              return const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()),
              );
            }

            // Load more when near the end
            if (index == searchState.results.length - 4 &&
                searchState.hasMore &&
                !searchState.isLoadingMore) {
              ref.read(bookSearchProvider.notifier).loadMore();
            }

            final book = searchState.results[index];
            final isAdded = book.isbn != null &&
                (searchState.shelfIsbns.contains(book.isbn) ||
                    shelfIsbns.contains(book.isbn));

            return Column(
              children: [
                _RegisterBookCard(
                  book: book,
                  isAdded: isAdded,
                  onTap: () => BookDetailBottomSheet.show(
                      context, resolveSheetBook(ref, book)),
                ),
                if (index < searchState.results.length - 1)
                  const Divider(height: 1, color: AppColors.border),
              ],
            );
          },
        );
    }
  }
}

// ─── Inline book card (48x70, radius 6) ───────────────────────────────────────

class _RegisterBookCard extends StatelessWidget {
  final Book book;
  final VoidCallback? onTap;
  final bool isAdded;

  const _RegisterBookCard({
    required this.book,
    this.onTap,
    this.isAdded = false,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Cover — 48x70
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: book.coverUrl != null && book.coverUrl!.isNotEmpty
                  ? Image.network(
                      book.coverUrl!,
                      width: 48,
                      height: 70,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) =>
                          _placeholderCover(),
                    )
                  : _placeholderCover(),
            ),
            const SizedBox(width: 14),

            // Title + author + year
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    book.title,
                    style: const TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w500,
                      color: AppColors.textPrimary,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (book.author != null && book.author!.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      displayAuthor(book.author),
                      style: const TextStyle(
                        fontSize: 13,
                        color: AppColors.textSecondary,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                  if (book.publisher != null && book.publisher!.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      book.publisher!,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ],
              ),
            ),

            // "등록됨" badge
            if (isAdded)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: AppColors.border,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Text(
                  '등록됨',
                  style: TextStyle(
                    fontSize: 11,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _placeholderCover() {
    return Container(
      width: 48,
      height: 70,
      decoration: BoxDecoration(
        color: AppColors.border,
        borderRadius: BorderRadius.circular(6),
      ),
      child: const Icon(Icons.book, color: AppColors.textSecondary, size: 20),
    );
  }
}

