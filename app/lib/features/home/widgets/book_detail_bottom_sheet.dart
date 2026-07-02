import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/models/book.dart';
import '../../../core/models/user_book.dart';
import '../../../core/services/impression_logger.dart';
import '../../../core/services/recommendation_service.dart';
import '../../../core/theme/app_colors.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../providers/recommendation_provider.dart';
import '../../../core/utils/author_format.dart';

/// 책 상세 바텀시트 — 커버 피드에서 탭했을 때 표시
class BookDetailBottomSheet extends ConsumerStatefulWidget {
  final Book book;

  const BookDetailBottomSheet({super.key, required this.book});

  static Future<void> show(BuildContext context, Book book) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => BookDetailBottomSheet(book: book),
    );
  }

  @override
  ConsumerState<BookDetailBottomSheet> createState() =>
      _BookDetailBottomSheetState();
}

class _BookDetailBottomSheetState extends ConsumerState<BookDetailBottomSheet> {
  bool _bookmarked = false;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    unawaited(
      ImpressionLogger(
        Supabase.instance.client,
      ).logAction(bookId: widget.book.id, action: 'clicked'),
    );
  }

  Future<void> _handleReading() async {
    if (_isLoading) return;
    // 사전 상태를 이미 알므로(userBookForProvider) 문구를 등록/전이로 구분한다.
    final wasShelved = ref.read(userBookForProvider(widget.book.id)) != null;
    setState(() => _isLoading = true);
    try {
      await addBookToShelf(ref, widget.book, BookStatus.reading);
      if (mounted) {
        Navigator.of(context).pop();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(wasShelved ? '읽는 중으로 옮겼어요' : '읽는 중으로 추가했어요')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('오류가 발생했어요: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _handleRead() async {
    if (_isLoading) return;
    setState(() => _isLoading = true);
    try {
      final userBookId = await addBookToShelf(
        ref,
        widget.book,
        BookStatus.read,
      );
      if (mounted) {
        Navigator.of(context).pop();
        context.push('/feedback/$userBookId');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('오류가 발생했어요: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  /// 서재 보유 책 삭제 — 이유를 묻지 않는 기본 동작(확인 다이얼로그 없음).
  /// 시트 닫기 → user_books 행 DELETE(스냅숏 확보) → 스낵바 "삭제했어요 · 실행 취소".
  Future<void> _handleDelete(UserBook userBook) async {
    final navigator = Navigator.of(context);
    final rootContext = navigator.context;
    // pop 전에 container 를 캡처 — 시트가 폐기된 뒤(지연 invalidate, 스낵바
    // 실행 취소)엔 이 위젯의 ref 를 쓸 수 없다(StateError).
    final container = ProviderScope.containerOf(context, listen: false);
    navigator.pop();

    final snapshot = await removeFromShelf(container, userBook);

    if (rootContext.mounted) {
      showDeletedSnackBar(
        rootContext,
        onUndo: () {
          unawaited(restoreToShelf(container, snapshot));
        },
      );
    }
  }

  /// 서재에 없는 책에 대한 "관심 없어요" — 취향 음수 신호(설계 §B).
  Future<void> _handleNotInterested(Book book) async {
    final navigator = Navigator.of(context);
    final rootContext = navigator.context;
    // pop 전에 container 캡처 — _handleDelete 와 동일한 이유.
    final container = ProviderScope.containerOf(context, listen: false);
    navigator.pop();

    final supabase = Supabase.instance.client;
    final userId = supabase.auth.currentUser?.id;
    if (userId != null) {
      try {
        await supabase.from('user_book_signals').insert({
          'user_id': userId,
          'book_id': book.id,
          'signal': 'not_interested',
        });
      } on PostgrestException catch (e) {
        // 23505 — 이미 마킹된 책. 조용히 무시.
        if (e.code != '23505') rethrow;
      } catch (_) {
        // 신호 저장 실패해도 로컬 필터/UX 는 계속 진행(fire-and-forget 성격).
      }
    }

    // 로컬 즉시 반영 — 재조회 전에도 카드가 바로 사라진다.
    container.read(hiddenBookIdsProvider.notifier).state = {
      ...container.read(hiddenBookIdsProvider),
      book.id,
    };

    unawaited(
      ImpressionLogger(supabase).logAction(bookId: book.id, action: 'disliked'),
    );
    unawaited(container.read(recommendationServiceProvider).triggerRecompute());
    unawaited(
      Future<void>.delayed(const Duration(seconds: 2)).then((_) {
        container.invalidate(recommendationsProvider);
      }),
    );

    if (rootContext.mounted) {
      showTimedSnackBar(
        rootContext,
        const SnackBar(content: Text('알겠어요, 이런 책은 덜 보여드릴게요')),
      );
    }
  }

  Future<void> _handleBookmark() async {
    if (_isLoading) return;
    // 이미 서재에 있으면 no-op — 데이터 계층(resolveShelfWrite)도 강등을 막지만,
    // 여기서 네트워크 없이 바로 안내한다(정직한 문구: "추가했어요" 오표기 방지).
    if (ref.read(userBookForProvider(widget.book.id)) != null) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('이미 서재에 있는 책이에요')));
      return;
    }
    final wasBookmarked = _bookmarked;
    setState(() {
      _bookmarked = !_bookmarked;
      _isLoading = true;
    });
    try {
      await addBookToShelf(ref, widget.book, BookStatus.wantToRead);
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('읽고싶은 책에 추가했어요')));
      }
    } catch (e) {
      // revert on error
      if (mounted) {
        setState(() => _bookmarked = wasBookmarked);
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('오류가 발생했어요: $e')));
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final book = widget.book;
    // 서재 상태 — 홈 진입 시 이미 로드된 bookshelfProvider 재사용(네트워크 0).
    // 등록/전이 후 addBookToShelf 가 invalidate 하므로 자동으로 최신화된다.
    final userBook = ref.watch(userBookForProvider(book.id));

    return Container(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      // 스크롤 + 최대높이: 미보유 책은 상태 버튼 3개로 시트가 길어져 하단
      // ('관심 없어요', 비슷한 책)이 화면 밖으로 넘치면 그려는 지되 터치가
      // 안 잡히는 문제가 있었다(Eden 실기기 리포트). 넘치면 스크롤로 도달.
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.85,
      ),
      child: SingleChildScrollView(
        child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 드래그 핸들
          const SizedBox(height: 12),
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.border,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 24),

          // 책 정보
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 표지
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: SizedBox(
                    width: 80,
                    height: 116,
                    child: book.coverUrl != null
                        ? Image.network(
                            book.coverUrl!,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) =>
                                _buildCoverPlaceholder(),
                          )
                        : _buildCoverPlaceholder(),
                  ),
                ),
                const SizedBox(width: 16),
                // 텍스트 정보
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        book.title,
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w500,
                          color: AppColors.textPrimary,
                          height: 1.3,
                        ),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 4),
                      if (book.author != null && book.author!.isNotEmpty)
                        Text(
                          displayAuthor(book.author),
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w300,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      // 서재 상태 배지 — "이미 내 서재에 있는 책"을 즉시 인지
                      if (userBook != null) ...[
                        const SizedBox(height: 8),
                        ShelfStatusBadge(userBook: userBook),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),

          // 설명
          if (book.description != null && book.description!.isNotEmpty) ...[
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Text(
                book.description!,
                style: const TextStyle(
                  fontSize: 13,
                  color: Color(0xFF64748B),
                  height: 1.5,
                ),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],

          const SizedBox(height: 24),

          // 액션 버튼 — 서재 상태에 따라 분기(Goodreads 패턴: 버튼이 곧 상태/다음 행동)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: ShelfAwareActions(
              userBook: userBook,
              isLoading: _isLoading,
              bookmarked:
                  _bookmarked || userBook?.status == BookStatus.wantToRead,
              onReading: _handleReading,
              onRead: _handleRead,
              onBookmark: _handleBookmark,
              onOpenFeedback: () {
                if (userBook != null) {
                  Navigator.of(context).pop();
                  context.push('/feedback/${userBook.id}');
                }
              },
            ),
          ),

          // destructive 액션 — 서재 보유 책은 삭제, 미보유 책은 관심없음.
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 8, 24, 0),
            child: userBook != null
                ? ShelfDeleteAction(
                    userBook: userBook,
                    onTap: () => unawaited(_handleDelete(userBook)),
                  )
                : NotInterestedAction(
                    onTap: () => unawaited(_handleNotInterested(book)),
                  ),
          ),

          // 비슷한 책 섹션
          _SimilarBooksSection(bookId: book.id),

          // 하단 안전 여백
          SizedBox(height: MediaQuery.of(context).padding.bottom + 24),
        ],
        ),
      ),
    );
  }

  Widget _buildCoverPlaceholder() {
    return Container(
      color: AppColors.shelf,
      child: const Icon(
        Icons.menu_book,
        color: AppColors.textSecondary,
        size: 32,
      ),
    );
  }
}

/// 서재 상태 배지 — 표지 옆 텍스트 영역에 "이미 내 서재에 있음"을 상시 노출.
/// (서재 경험 가치: 꽂아둔 책이라는 사실 자체가 뿌듯함의 일부)
class ShelfStatusBadge extends StatelessWidget {
  final UserBook userBook;

  const ShelfStatusBadge({super.key, required this.userBook});

  String get _label {
    switch (userBook.status) {
      case BookStatus.wantToRead:
        return '🔖 찜한 책';
      case BookStatus.reading:
        return '📖 읽는 중';
      case BookStatus.read:
        if (userBook.rating == 'good') return '✓ 읽은 책 · 좋았어요';
        if (userBook.rating == 'bad') return '✓ 읽은 책 · 아쉬웠어요';
        return '✓ 읽은 책';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '서재 상태: $_label',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: AppColors.shelf.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(
          _label,
          style: const TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w500,
            color: AppColors.textSecondary,
          ),
        ),
      ),
    );
  }
}

/// 서재 상태별 액션 버튼 영역 (Goodreads 패턴: 버튼 = 현재 상태에서의 다음 행동).
///
/// - 서재에 없음: [읽는 중] [읽었어요] [🔖]
/// - 찜(wishlist): 동일 + 🔖 filled — 읽는중/읽었어요는 상태 전이로 동작
/// - 읽는 중: [다 읽었어요] 단독 → 읽음 전이 + 피드백
/// - 읽은 책: [내 평가 보기 · 수정 | 평가 남기기] → 피드백 화면(재등록 대신 루프 닫기)
class ShelfAwareActions extends StatelessWidget {
  final UserBook? userBook;
  final bool isLoading;
  final bool bookmarked;
  final VoidCallback onReading;
  final VoidCallback onRead;
  final VoidCallback onBookmark;
  final VoidCallback onOpenFeedback;

  const ShelfAwareActions({
    super.key,
    required this.userBook,
    required this.isLoading,
    required this.bookmarked,
    required this.onReading,
    required this.onRead,
    required this.onBookmark,
    required this.onOpenFeedback,
  });

  @override
  Widget build(BuildContext context) {
    switch (userBook?.status) {
      case BookStatus.reading:
        return _ActionButton(
          label: '다 읽었어요',
          isPrimary: true,
          isLoading: isLoading,
          onTap: onRead,
        );
      case BookStatus.read:
        return _ActionButton(
          label: userBook!.rating != null ? '내 평가 보기 · 수정' : '평가 남기기',
          isPrimary: true,
          isLoading: isLoading,
          onTap: onOpenFeedback,
        );
      case BookStatus.wantToRead:
      case null:
        return Row(
          children: [
            Expanded(
              child: _ActionButton(
                label: '읽는 중',
                isPrimary: false,
                isLoading: isLoading,
                onTap: onReading,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: _ActionButton(
                label: '읽었어요',
                isPrimary: true,
                isLoading: isLoading,
                onTap: onRead,
              ),
            ),
            const SizedBox(width: 8),
            _BookmarkButton(
              bookmarked: bookmarked,
              isLoading: isLoading,
              onTap: onBookmark,
            ),
          ],
        );
    }
  }
}

/// 서재 보유 책 삭제 버튼 — destructive 텍스트 버튼(확인 다이얼로그 없음, 설계
/// §A "이유를 묻지 않는 기본 동작"). status=wishlist 면 라벨만 "읽고 싶어요
/// 취소"(왓챠 토글 해제 패턴), 그 외 상태는 "이 책 삭제".
class ShelfDeleteAction extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback onTap;

  const ShelfDeleteAction({
    super.key,
    required this.userBook,
    required this.onTap,
  });

  String get _label =>
      userBook.status == BookStatus.wantToRead ? '읽고 싶어요 취소' : '이 책 삭제';

  @override
  Widget build(BuildContext context) {
    return Center(
      child: TextButton(
        onPressed: onTap,
        style: TextButton.styleFrom(foregroundColor: AppColors.error),
        child: Text(
          _label,
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
        ),
      ),
    );
  }
}

/// 서재에 없는 책의 "관심 없어요" 버튼 — 보조 스타일(삭제보다 약한 톤).
class NotInterestedAction extends StatelessWidget {
  final VoidCallback onTap;

  const NotInterestedAction({super.key, required this.onTap});

  @override
  Widget build(BuildContext context) {
    // 회색 텍스트만 있으면 비활성 버튼처럼 보인다(Eden 리포트) — 아웃라인으로
    // "눌리는 것"임을 드러내되 destructive 톤은 피한다(보조 액션).
    return Center(
      child: OutlinedButton(
        onPressed: onTap,
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.textSecondary,
          side: const BorderSide(color: AppColors.border),
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20),
          ),
        ),
        child: const Text(
          '관심 없어요',
          style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
        ),
      ),
    );
  }
}

/// 스낵바를 띄우고 [duration] 후 **타이머로 강제 해제**한다.
///
/// iOS 보조 내비게이션(AssistiveTouch/VoiceOver → accessibleNavigation=true)
/// 환경에서 Flutter 는 액션 있는 스낵바를 자동 해제하지 않는다 — '삭제했어요'가
/// 영원히 남고, 큐에 쌓인 다음 스낵바('알겠어요…')까지 막던 실기기 문제(Eden
/// 리포트)의 근본수정. 표시 전에 기존 스낵바도 청소해 큐 블로킹을 끊는다.
void showTimedSnackBar(
  BuildContext context,
  SnackBar bar, {
  Duration duration = const Duration(seconds: 4),
}) {
  final messenger = ScaffoldMessenger.of(context);
  messenger.clearSnackBars();
  final controller = messenger.showSnackBar(bar);
  Timer(duration, controller.close);
}

/// 삭제 직후 스낵바 — "삭제했어요" + "실행 취소" 액션. [onUndo] 는 탭 시
/// 호출측(restoreToShelf 등)이 스냅숏 복원을 담당한다. 4초 후 자동 해제.
void showDeletedSnackBar(BuildContext context, {required VoidCallback onUndo}) {
  showTimedSnackBar(
    context,
    SnackBar(
      content: const Text('삭제했어요'),
      action: SnackBarAction(label: '실행 취소', onPressed: onUndo),
    ),
  );
}

class _ActionButton extends StatelessWidget {
  final String label;
  final bool isPrimary;
  final bool isLoading;
  final VoidCallback onTap;

  const _ActionButton({
    required this.label,
    required this.isPrimary,
    required this.isLoading,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: isLoading ? null : onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: isPrimary ? AppColors.primary : const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(10),
          border: isPrimary ? null : Border.all(color: const Color(0xFFF1F5F9)),
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: isPrimary ? AppColors.textOnPrimary : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 비슷한 책 섹션
// ---------------------------------------------------------------------------

class _SimilarBooksSection extends ConsumerWidget {
  final String bookId;

  const _SimilarBooksSection({required this.bookId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // book_id가 비어있으면 (예: 추천 서버에서 온 임시 ID가 아직 없으면) 숨김
    if (bookId.isEmpty) return const SizedBox.shrink();

    final similarAsync = ref.watch(similarBooksProvider(bookId));
    final hidden = ref.watch(hiddenBookIdsProvider);

    return similarAsync.when(
      loading: () => const Padding(
        padding: EdgeInsets.symmetric(vertical: 16),
        child: Center(
          child: SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: AppColors.textSecondary,
            ),
          ),
        ),
      ),
      error: (_, __) => const SizedBox.shrink(),
      data: (allBooks) {
        final books = hidden.isEmpty
            ? allBooks
            : allBooks.where((b) => !hidden.contains(b.bookId)).toList();
        if (books.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(24, 24, 24, 10),
              child: Text(
                '비슷한 책',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                  letterSpacing: 0.01,
                ),
              ),
            ),
            SizedBox(
              height: 124,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 24),
                itemCount: books.length,
                separatorBuilder: (_, __) => const SizedBox(width: 10),
                itemBuilder: (context, index) {
                  final similar = books[index];
                  return _SimilarBookCard(
                    book: similar,
                    onTap: () {
                      Navigator.pop(context);
                      BookDetailBottomSheet.show(context, similar.toBook());
                    },
                  );
                },
              ),
            ),
          ],
        );
      },
    );
  }
}

class _SimilarBookCard extends StatelessWidget {
  final RecommendedBook book;
  final VoidCallback onTap;

  const _SimilarBookCard({required this.book, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 72,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: SizedBox(
                width: 72,
                height: 104,
                child: book.coverUrl != null
                    ? Image.network(
                        book.coverUrl!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _SimilarCoverFallback(),
                      )
                    : _SimilarCoverFallback(),
              ),
            ),
            const SizedBox(height: 4),
            Text(
              book.title,
              style: const TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
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

class _SimilarCoverFallback extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.shelf,
      child: const Icon(
        Icons.menu_book,
        color: AppColors.textSecondary,
        size: 20,
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _BookmarkButton extends StatelessWidget {
  final bool bookmarked;
  final bool isLoading;
  final VoidCallback onTap;

  const _BookmarkButton({
    required this.bookmarked,
    required this.isLoading,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: isLoading ? null : onTap,
      child: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          color: const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: const Color(0xFFF1F5F9)),
        ),
        alignment: Alignment.center,
        child: Icon(
          bookmarked ? Icons.bookmark : Icons.bookmark_border,
          color: AppColors.textPrimary,
          size: 20,
        ),
      ),
    );
  }
}
