import 'package:flutter/material.dart';
import '../models/user_book.dart';
import '../theme/app_colors.dart';
import 'book_spine.dart';

class BookshelfRow extends StatefulWidget {
  final List<UserBook> userBooks;
  final void Function(UserBook userBook)? onBookTap;
  final void Function(List<UserBook> reordered)? onReorder;

  const BookshelfRow({
    super.key,
    required this.userBooks,
    this.onBookTap,
    this.onReorder,
  });

  @override
  State<BookshelfRow> createState() => _BookshelfRowState();
}

class _BookshelfRowState extends State<BookshelfRow> {
  late List<UserBook> _items;
  int? _dragIndex;

  @override
  void initState() {
    super.initState();
    _items = widget.userBooks.where((ub) => ub.book != null).toList();
  }

  @override
  void didUpdateWidget(BookshelfRow oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.userBooks != widget.userBooks) {
      _items = widget.userBooks.where((ub) => ub.book != null).toList();
    }
  }

  void _onReorder(int oldIndex, int newIndex) {
    setState(() {
      final item = _items.removeAt(oldIndex);
      _items.insert(newIndex, item);
      _dragIndex = null;
    });
    widget.onReorder?.call(List.from(_items));
  }

  @override
  Widget build(BuildContext context) {
    if (_items.isEmpty) {
      return Column(
        children: [
          SizedBox(height: 200, child: _emptyRow(context)),
          _plank(),
        ],
      );
    }
    // 화면 폭에 맞춰 책을 여러 선반으로 나눈다 — 가로 무한 스크롤이 아니라 진짜 책장처럼
    // 한 선반이 차면 아래 선반으로 줄바꿈(세로 스크롤). PRODUCT_PLAN 5-3.
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxW = constraints.maxWidth - 32; // 좌우 여백
        final shelves = <List<int>>[];
        var row = <int>[];
        var w = 0.0;
        for (var i = 0; i < _items.length; i++) {
          final sw = _spineFootprint(_items[i]);
          if (row.isNotEmpty && w + sw > maxW) {
            shelves.add(row);
            row = <int>[];
            w = 0;
          }
          row.add(i);
          w += sw;
        }
        if (row.isNotEmpty) shelves.add(row);

        return Column(
          children: [
            for (final shelf in shelves) ...[
              SizedBox(
                height: 200,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      for (final index in shelf)
                        _DraggableSpine(
                          index: index,
                          userBook: _items[index],
                          isDragging: _dragIndex == index,
                          onTap: widget.onBookTap != null
                              ? () => widget.onBookTap!(_items[index])
                              : null,
                          onDragStarted: () =>
                              setState(() => _dragIndex = index),
                          onDragEnd: () => setState(() => _dragIndex = null),
                          onAccept: (fromIndex) => _onReorder(fromIndex, index),
                        ),
                    ],
                  ),
                ),
              ),
              _plank(),
              const SizedBox(height: 22),
            ],
          ],
        );
      },
    );
  }

  /// 책등 1개가 선반에서 차지하는 가로 폭(책등 폭 + 좌우 패딩).
  double _spineFootprint(UserBook ub) {
    final pages = ub.book?.pageCount ?? 250;
    return (pages / 8).clamp(30, 50).toDouble() + 4;
  }

  /// 선반(나무 라인).
  Widget _plank() {
    return Container(
      height: 10,
      margin: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: AppColors.shelf,
        borderRadius: const BorderRadius.vertical(bottom: Radius.circular(4)),
        boxShadow: [
          BoxShadow(
            color: AppColors.shelfDark.withValues(alpha: 0.5),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
    );
  }

  Widget _emptyRow(BuildContext context) {
    return Center(
      child: Container(
        width: 80,
        height: 190,
        decoration: BoxDecoration(
          border: Border.all(
            color: AppColors.textSecondary.withValues(alpha: 0.3),
            style: BorderStyle.solid,
          ),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Icon(
          Icons.add,
          color: AppColors.textSecondary.withValues(alpha: 0.4),
          size: 24,
        ),
      ),
    );
  }
}

class _DraggableSpine extends StatelessWidget {
  final int index;
  final UserBook userBook;
  final bool isDragging;
  final VoidCallback? onTap;
  final VoidCallback onDragStarted;
  final VoidCallback onDragEnd;
  final void Function(int fromIndex) onAccept;

  const _DraggableSpine({
    required this.index,
    required this.userBook,
    required this.isDragging,
    this.onTap,
    required this.onDragStarted,
    required this.onDragEnd,
    required this.onAccept,
  });

  @override
  Widget build(BuildContext context) {
    return DragTarget<int>(
      onWillAcceptWithDetails: (details) => details.data != index,
      onAcceptWithDetails: (details) => onAccept(details.data),
      builder: (context, candidateData, rejectedData) {
        final isHovered = candidateData.isNotEmpty;
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 2),
          child: LongPressDraggable<int>(
            data: index,
            onDragStarted: onDragStarted,
            onDragEnd: (_) => onDragEnd(),
            onDraggableCanceled: (_, __) => onDragEnd(),
            feedback: Material(
              color: Colors.transparent,
              child: Opacity(
                opacity: 0.8,
                child: BookSpine(book: userBook.book!, height: 190),
              ),
            ),
            childWhenDragging: Opacity(
              opacity: 0.3,
              child: BookSpine(book: userBook.book!, height: 190),
            ),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              transform: isHovered
                  ? (Matrix4.identity()..translate(6.0, 0.0))
                  : Matrix4.identity(),
              child: BookSpine(
                book: userBook.book!,
                onTap: onTap,
              ),
            ),
          ),
        );
      },
    );
  }
}
