import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/services/impression_logger.dart';

void main() {
  group('ImpressionLogger', () {
    test('logImpressions builds rows with correct shape', () {
      final rows = ImpressionLogger.buildImpressionRows(
        userId: 'u1',
        bookIds: const ['b1', 'b2', 'b3'],
        source: 'home_recommend',
        algorithmVersion: 'h10_stage0',
        sessionId: 'sess-1',
      );
      expect(rows, hasLength(3));
      expect(rows[0]['user_id'], 'u1');
      expect(rows[0]['book_id'], 'b1');
      expect(rows[0]['position'], 0);
      expect(rows[1]['position'], 1);
      expect(rows[0]['source'], 'home_recommend');
      expect(rows[0]['algorithm_version'], 'h10_stage0');
      expect(rows[0]['session_id'], 'sess-1');
      expect(rows[0].containsKey('action'), isFalse);
    });

    test('buildActionUpdate returns action + action_at fields', () {
      final patch = ImpressionLogger.buildActionUpdate(action: 'clicked');
      expect(patch['action'], 'clicked');
      expect(patch['action_at'], isA<String>());
    });

    test('buildActionUpdate rejects invalid action', () {
      expect(
        () => ImpressionLogger.buildActionUpdate(action: 'wat'),
        throwsArgumentError,
      );
    });
  });
}
