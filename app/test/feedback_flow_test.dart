import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/services/recommendation_service.dart';
import 'package:curation_app/features/feedback/providers/feedback_flow_provider.dart';
import 'package:curation_app/features/home/providers/recommendation_provider.dart';

/// FeedbackFlowNotifier 는 PostgREST(user_books select/update) 를 직접 호출한다
/// (프로젝트에 mockito/mocktail 이 없고, Supabase 쿼리 빌더는 인터페이스 추출 없이
/// mock 불가) — 로컬 HTTP 서버로 실제 SupabaseClient 가 때리는 요청에 200 을
/// 응답해 submit() 을 끝까지 실행시키고, 그 뒤의 2초 지연 invalidate 를 검증한다.
class _FakePostgrest {
  late HttpServer _server;
  String get baseUrl => 'http://${_server.address.host}:${_server.port}';

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((req) async {
      req.response.headers.contentType = ContentType.json;
      if (req.method == 'GET' && req.uri.path.contains('/user_books')) {
        // FeedbackFlowNotifier._load() 의 .single() 응답.
        req.response.write(jsonEncode({
          'id': 'ub1',
          'user_id': 'u1',
          'book_id': 'b1',
          'status': 'finished',
          'rating': null,
          'emotion_tags': null,
          'review_text': null,
        }));
      } else {
        // update 등 나머지 쓰기 요청 — 빈 배열 응답으로 충분(코드가 body 를 안 씀).
        req.response.write(jsonEncode([]));
      }
      await req.response.close();
    });
  }

  Future<void> stop() => _server.close(force: true);
}

/// triggerRecompute 호출만 무해하게 흡수하는 가짜 추천 서비스.
class _FakeRecommendationService extends RecommendationService {
  @override
  Future<void> triggerRecompute() async {}
}

void main() {
  late _FakePostgrest fakeServer;
  late SupabaseClient client;

  setUp(() async {
    fakeServer = _FakePostgrest();
    await fakeServer.start();
    client = SupabaseClient(
      fakeServer.baseUrl,
      'anon-key',
      authOptions: const AuthClientOptions(autoRefreshToken: false),
    );
  });

  tearDown(() async {
    await fakeServer.stop();
  });

  group('변경 5 — 피드백 submit 후 추천 섹션 자동 갱신', () {
    test('submit 직후엔 invalidate 되지 않고, 2초 뒤에 recommendationsProvider 가 무효화된다',
        () async {
      // feedbackFlowProvider 는 기본적으로 Supabase.instance.client 를 쓰는
      // FeedbackFlowNotifier(ref, userBookId) 를 생성한다 — 테스트에서 로컬 서버를
      // 가리키는 client 를 주입하려면 provider 자체를 override 한다.
      final testProvider = StateNotifierProvider.family<FeedbackFlowNotifier,
          FeedbackFlowState, String>((ref, userBookId) {
        return FeedbackFlowNotifier(ref, userBookId, client);
      });

      final container = ProviderContainer(overrides: [
        recommendationServiceProvider
            .overrideWithValue(_FakeRecommendationService()),
        // recommendationsProvider 재빌드 시 실제 HTTP(Supabase.instance 미초기화)를
        // 타지 않도록 고정값으로 대체 — invalidate 여부(재빌드 횟수)만 검증한다.
        recommendationsProvider.overrideWith((ref) async =>
            const RecommendationResult(
                recommendations: [], hasFeedback: false, totalLiked: 0)),
      ]);
      addTearDown(container.dispose);

      final notifier = container.read(testProvider('ub1').notifier);

      // 로드(_load) 완료 대기.
      await Future<void>.delayed(const Duration(milliseconds: 100));
      notifier.setRating('good');

      // 최초 구독으로 인한 초기 빌드가 지나간 뒤부터 재빌드(invalidate) 횟수를 센다.
      // (container.listen 등록 자체가 provider 를 구독시켜 loading→data 전이를
      // 한 번 리스너에 통지하므로, 그 이후 변화만 "재조회"로 간주한다.)
      await container.read(recommendationsProvider.future);
      var recommendationsBuildCount = 0;
      container.listen(
        recommendationsProvider,
        (prev, next) => recommendationsBuildCount++,
      );

      await notifier.submit();

      // submit 직후엔 아직 invalidate 되지 않는다(2초 지연 설계 — recompute 가
      // 서버에 computing 플래그를 세울 시간을 준다).
      expect(recommendationsBuildCount, 0);

      // 2초 지연 이후엔 invalidate 되어 recommendationsProvider 가 재빌드된다.
      await Future<void>.delayed(const Duration(seconds: 2, milliseconds: 300));
      expect(recommendationsBuildCount, greaterThan(0));
    });
  });
}
