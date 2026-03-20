import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/routing/app_router.dart';

void main() {
  testWidgets('HomePage displays Curation text', (WidgetTester tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: HomePage(),
      ),
    );

    expect(find.text('Curation'), findsOneWidget);
  });
}
