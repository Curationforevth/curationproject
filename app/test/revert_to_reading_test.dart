import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';

void main() {
  test('revertToReadingPayload — status reading + 평가필드 전부 null', () {
    final p = revertToReadingPayload();
    expect(p['status'], 'reading');
    expect(p.containsKey('rating'), isTrue);
    expect(p['rating'], isNull);
    expect(p['emotion_tags'], isNull);
    expect(p['review_text'], isNull);
  });
}
