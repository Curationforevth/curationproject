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

/// 책 상세 바텀시트 — 커버 피드에서 탭했을 때 표시.
/// 2단계 확장: peek(0.65) ↔ 확장(1.0), DraggableScrollableSheet 기반.
class BookDetailBottomSheet extends ConsumerStatefulWidget {
  final Book book;

  const BookDetailBottomSheet({super.key, required this.book});

  static Future<void> show(BuildContext context, Book book) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      // 모달 자체 드래그를 끈다 — 내부 DraggableScrollableSheet 와 제스처가
      // 경합해 핸들을 끌면 리사이즈 대신 모달 통째 dismiss 되는 오동작 방지.
      // 닫기 = 바깥 탭 / 최소 높이까지 드래그(NotificationListener pop) / 확장 헤더 ✕.
      enableDrag: false,
      backgroundColor: Colors.transparent,
      builder: (_) => BookDetailBottomSheet(book: book),
    );
  }

  @override
  ConsumerState<BookDetailBottomSheet> createState() =>
      _BookDetailBottomSheetState();
}

class _BookDetailBottomSheetState extends ConsumerState<BookDetailBottomSheet> {
  static const _peekSize = 0.65;
  static const _minSize = 0.4;

  bool _bookmarked = false;
  bool _isLoading = false;
  bool _expanded = false;
  // 드래그로 최소 높이 도달 → pop 은 1회만. 알림은 드래그 내내 연속 발화하므로
  // 가드 없이는 두 번째 pop 이 모달 아래 화면(홈)까지 닫아버린다.
  bool _dismissing = false;
  final _sheetController = DraggableScrollableController();

  @override
  void initState() {
    super.initState();
    // 임프레션: provider 시임 경유(테스트 가능성) + DB 미등록 책(id='') 스킵
    if (widget.book.id.isNotEmpty) {
      unawaited(
        ref
            .read(impressionLoggerProvider)
            .logAction(bookId: widget.book.id, action: 'clicked'),
      );
    }
    _sheetController.addListener(() {
      final expanded = _sheetController.size > 0.9;
      if (expanded != _expanded) setState(() => _expanded = expanded);
    });
  }

  @override
  void dispose() {
    _sheetController.dispose();
    super.dispose();
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

    return NotificationListener<DraggableScrollableNotification>(
      onNotification: (n) {
        if (!_dismissing && n.extent <= _minSize + 0.01) {
          _dismissing = true;
          Navigator.of(context).pop();
        }
        return false;
      },
      child: DraggableScrollableSheet(
        controller: _sheetController,
        expand: false,
        initialChildSize: _peekSize,
        minChildSize: _minSize,
        maxChildSize: 1.0,
        snap: true,
        snapSizes: const [_peekSize],
        builder: (context, scrollController) => AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.vertical(
              top: Radius.circular(_expanded ? 0 : 20),
            ),
          ),
          child: Column(
            children: [
              // 확장 헤더만 스크롤 밖 고정. peek 핸들은 스크롤 안 첫 요소 —
              // DraggableScrollableSheet 는 제공된 scrollController 의 스크롤러블
              // 위 드래그로만 extent 를 움직이므로, 핸들이 밖에 있으면 죽은 드래그가 된다.
              if (_expanded) _buildExpandedHeader(book),
              Expanded(
                child: SingleChildScrollView(
                  controller: scrollController,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (!_expanded) _buildPeekHandle(),

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
                                  if (book.author != null &&
                                      book.author!.isNotEmpty)
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
                      if (book.description != null &&
                          book.description!.isNotEmpty) ...[
                        const SizedBox(height: 16),
                        Padding(
                          padding:
                              const EdgeInsets.symmetric(horizontal: 24),
                          child: Text(
                            book.description!,
                            key: const Key('sheet_description'),
                            style: const TextStyle(
                              fontSize: 13,
                              color: Color(0xFF64748B),
                              height: 1.5,
                            ),
                            maxLines: _expanded ? null : 3,
                            overflow:
                                _expanded ? null : TextOverflow.ellipsis,
                          ),
                        ),
                      ],

                      const SizedBox(height: 24),

                      // 평가를 남긴 읽은 책 = 내 평가 조회가 곧 이 시트의 본문
                      // (/book 페이지의 조회 역할 흡수 — 핵심가치 ② 취향 발견).
                      // 그 외 상태 = 상태별 다음 행동 버튼(Goodreads 패턴).
                      if (userBook?.status == BookStatus.read &&
                          userBook!.rating != null)
                        MyRatingSection(
                          userBook: userBook,
                          onEdit: () {
                            Navigator.of(context).pop();
                            context.push('/feedback/${userBook.id}');
                          },
                        )
                      else
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 24),
                          child: ShelfAwareActions(
                            userBook: userBook,
                            isLoading: _isLoading,
                            bookmarked: _bookmarked ||
                                userBook?.status == BookStatus.wantToRead,
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
                        padding:
                            const EdgeInsets.fromLTRB(24, 8, 24, 0),
                        child: userBook != null
                            ? ShelfDeleteAction(
                                userBook: userBook,
                                onTap: () =>
                                    unawaited(_handleDelete(userBook)),
                              )
                            : NotInterestedAction(
                                onTap: () => unawaited(
                                    _handleNotInterested(book)),
                              ),
                      ),

                      // 비슷한 책 섹션
                      _SimilarBooksSection(
                        bookId: book.id,
                        expanded: _expanded,
                      ),

                      // 하단 안전 여백
                      SizedBox(
                        height:
                            MediaQuery.of(context).padding.bottom + 24,
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
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

  /// peek 핸들: 드래그바 하나만 중앙에. 별도 셰브론 아이콘은 제목과 겹쳐
  /// 지저분해 보여 제거(Eden 실기기 리포트) — 대신 핸들 영역 전체가 탭
  /// 타깃(접근성: 드래그 없이 탭으로 확장, Semantics 버튼).
  Widget _buildPeekHandle() {
    return Semantics(
      button: true,
      label: '크게 보기',
      child: GestureDetector(
        key: const Key('sheet_expand_chevron'),
        behavior: HitTestBehavior.opaque,
        onTap: () => _sheetController.animateTo(
          1.0,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        ),
        child: SizedBox(
          height: 28,
          child: Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.border,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// 확장 헤더: 제목 + ✕ (Google Maps 모프 패턴)
  Widget _buildExpandedHeader(Book book) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 8, 8, 0),
      child: Row(
        children: [
          Expanded(
            child: Text(
              book.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: AppColors.textPrimary,
              ),
            ),
          ),
          IconButton(
            key: const Key('sheet_close_button'),
            icon: const Icon(Icons.close, color: AppColors.textSecondary),
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
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
    // NotInterestedAction 과 동일한 조용한 텍스트 액션 톤 — destructive 라
    // 색만 error, 밀도/높이는 통일.
    return Center(
      child: TextButton.icon(
        onPressed: onTap,
        style: TextButton.styleFrom(
          foregroundColor: AppColors.error,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        ),
        icon: Icon(
          userBook.status == BookStatus.wantToRead
              ? Icons.bookmark_remove_outlined
              : Icons.delete_outline,
          size: 15,
        ),
        label: Text(
          _label,
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
        ),
      ),
    );
  }
}

/// 읽은 책의 내 평가(호오·감정태그·한 줄 감상) 카드 — 시트에서 바로 조회.
/// /book 페이지 진입로가 사라진 뒤 평가 조회 저니의 정본 위치.
class MyRatingSection extends StatelessWidget {
  final UserBook userBook;
  final VoidCallback onEdit;

  const MyRatingSection({
    super.key,
    required this.userBook,
    required this.onEdit,
  });

  @override
  Widget build(BuildContext context) {
    final tags = userBook.emotionTags ?? const <String>[];
    final review = userBook.reviewText;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(horizontal: 24),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      decoration: BoxDecoration(
        color: const Color(0xFFF8FAFC),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFF1F5F9)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Text(
                '내 평가',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textSecondary,
                ),
              ),
              const Spacer(),
              GestureDetector(
                onTap: onEdit,
                behavior: HitTestBehavior.opaque,
                child: const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                  child: Text(
                    '수정',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w500,
                      color: AppColors.primary,
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            userBook.rating == 'good' ? '👍 좋았어요' : '👎 아쉬웠어요',
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
          if (tags.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final t in tags)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppColors.shelf.withValues(alpha: 0.5),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      '#$t',
                      style: const TextStyle(
                        fontSize: 11,
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ),
              ],
            ),
          ],
          if (review != null && review.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              review,
              style: const TextStyle(
                fontSize: 13,
                height: 1.5,
                color: AppColors.textPrimary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 서재에 없는 책의 "관심 없어요".
///
/// 스타일 변천(전부 Eden 실기기 기각): 회색 텍스트=비활성처럼 보임 →
/// 아웃라인 pill=입력 필드처럼 보임 → 조용한 텍스트 액션=클리커블로 안 보임.
/// 결론: 새 스타일을 발명하지 않고 **같은 시트의 보조 버튼('읽는 중',
/// _ActionButton isPrimary:false)과 동일한 시각 언어**를 쓴다 — 같은 화면의
/// 버튼과 생김새가 같아야 버튼으로 읽힌다.
class NotInterestedAction extends StatelessWidget {
  final VoidCallback onTap;

  const NotInterestedAction({super.key, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
          decoration: BoxDecoration(
            color: const Color(0xFFF8FAFC),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: const Color(0xFFF1F5F9)),
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.visibility_off_outlined,
                  size: 15, color: AppColors.textSecondary),
              SizedBox(width: 6),
              Text(
                '관심 없어요',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
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
  final bool expanded;

  const _SimilarBooksSection({required this.bookId, this.expanded = false});

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

        void openSimilar(RecommendedBook similar) {
          Navigator.pop(context);
          BookDetailBottomSheet.show(context, similar.toBook());
        }

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
            if (expanded)
              GridView.builder(
                key: const Key('sheet_similar_grid'),
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                padding: const EdgeInsets.symmetric(horizontal: 24),
                gridDelegate:
                    const SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 2,
                  childAspectRatio: 0.52,
                  crossAxisSpacing: 12,
                  mainAxisSpacing: 16,
                ),
                itemCount: books.length,
                itemBuilder: (context, index) {
                  final similar = books[index];
                  return _SimilarBookCard(
                    book: similar,
                    width: null,
                    onTap: () => openSimilar(similar),
                  );
                },
              )
            else
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
                      onTap: () => openSimilar(similar),
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
  final double? width;

  const _SimilarBookCard({
    required this.book,
    required this.onTap,
    this.width = 72,
  });

  @override
  Widget build(BuildContext context) {
    final content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: AspectRatio(
            aspectRatio: 72 / 104,
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
    );

    return GestureDetector(
      onTap: onTap,
      child: width != null ? SizedBox(width: width, child: content) : content,
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
