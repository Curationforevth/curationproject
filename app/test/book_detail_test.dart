import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/features/book_detail/widgets/rating_selector.dart';
import 'package:curation_app/features/book_detail/widgets/emotion_tag_chips.dart';
import 'package:curation_app/features/book_detail/widgets/review_text_section.dart';
import 'package:curation_app/core/models/emotion_tag.dart';

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

    testWidgets('calls onToggle when tapped', (tester) async {
      String? toggled;

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: EmotionTagChips(
              options: [
                EmotionTag(id: '1', label: '잔잔한', sortOrder: 1, isActive: true),
              ],
              selectedIds: [],
              onToggle: (id) => toggled = id,
            ),
          ),
        ),
      );

      await tester.tap(find.text('잔잔한'));
      expect(toggled, '1');
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
