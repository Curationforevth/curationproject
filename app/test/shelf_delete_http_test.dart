import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/core/models/user_book.dart';
import 'package:curation_app/features/bookshelf/providers/bookshelf_provider.dart';

/// removeFromShelf/restoreToShelf 의 실제 Supabase 호출부 검증 — 설계:
/// docs/superpowers/specs/2026-07-02-shelf-remove-not-interested-design.md
///
/// test/bookshelf_test.dart 와 동일 이유로 별도 파일: 이 스위트는 testWidgets 를
/// 쓰지 않는다(TestWidgetsFlutterBinding 이 있으면 실 HttpClient 요청이 전부
/// 400 으로 가로채져 로컬 가짜 서버 응답이 앱에 닿지 못한다).
class _FakePostgrest {
  late HttpServer _server;
  int deleteCalls = 0;
  int insertCalls = 0;
  Map<String, dynamic>? lastInsertBody;
  bool conflictOnce = false;

  String get baseUrl => 'http://${_server.address.host}:${_server.port}';

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((req) async {
      final bodyStr = await utf8.decodeStream(req);
      req.response.headers.contentType = ContentType.json;

      if (req.method == 'DELETE' && req.uri.path.contains('/user_books')) {
        deleteCalls++;
        req.response.write(jsonEncode([]));
      } else if (req.method == 'POST' && req.uri.path.contains('/user_books')) {
        insertCalls++;
        final decoded = bodyStr.isEmpty
            ? <String, dynamic>{}
            : jsonDecode(bodyStr) as Map<String, dynamic>;
        lastInsertBody = decoded;
        if (conflictOnce && decoded.containsKey('id')) {
          conflictOnce = false;
          req.response.statusCode = 409;
          req.response.write(
            jsonEncode({
              'code': '23505',
              'message': 'duplicate key value violates unique constraint',
            }),
          );
        } else {
          req.response.write(jsonEncode([decoded]));
        }
      } else {
        req.response.write(jsonEncode([]));
      }
      await req.response.close();
    });
  }

  Future<void> stop() => _server.close(force: true);
}

UserBook _ub(BookStatus status, {String? rating, String id = 'ub-1'}) =>
    UserBook(
      id: id,
      userId: 'u1',
      bookId: 'b1',
      status: status,
      rating: rating,
    );

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

  group('변경 4 — removeFromShelf/restoreToShelf 통합(가짜 PostgREST)', () {
    test('removeFromShelf 는 DELETE 요청을 보내고 스냅숏을 반환한다', () async {
      final ub = _ub(BookStatus.read, rating: 'good');
      final snapshot = await removeFromShelfWith(client, ub);

      expect(fakeServer.deleteCalls, 1);
      expect(snapshot['id'], 'ub-1');
      expect(snapshot['rating'], 'good');
    });

    test('restoreToShelf 는 스냅숏으로 재 INSERT 한다(id 포함 우선 시도)', () async {
      final ub = _ub(BookStatus.read, rating: 'good');
      final snapshot = userBookSnapshot(ub);

      await restoreToShelfWith(client, snapshot);

      expect(fakeServer.insertCalls, 1);
      expect(fakeServer.lastInsertBody?['id'], 'ub-1');
    });

    test('restoreToShelf 는 23505 를 받으면 id 제외 재삽입으로 폴백한다', () async {
      fakeServer.conflictOnce = true;
      final ub = _ub(BookStatus.read, rating: 'good');
      final snapshot = userBookSnapshot(ub);

      await restoreToShelfWith(client, snapshot);

      // 1차(id 포함, 409) + 2차(id 제외, 성공) = insert 시도 2회.
      expect(fakeServer.insertCalls, 2);
      expect(fakeServer.lastInsertBody?.containsKey('id'), isFalse);
    });
  });
}
