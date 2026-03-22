import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:curation_app/features/auth/providers/auth_provider.dart';
import 'package:curation_app/features/auth/screens/login_screen.dart';
import 'package:curation_app/features/auth/services/auth_service.dart';

class FakeAuthService implements AuthService {
  @override
  Future<AuthResponse> signInWithKakao() async =>
      throw UnimplementedError('test stub');

  @override
  Future<void> signInWithGoogle() async =>
      throw UnimplementedError('test stub');

  @override
  Future<void> signOut() async => throw UnimplementedError('test stub');

  @override
  Session? get currentSession => null;

  @override
  Stream<AuthState> get onAuthStateChange => const Stream.empty();
}

void main() {
  testWidgets('LoginScreen displays social login buttons',
      (WidgetTester tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authServiceProvider.overrideWithValue(FakeAuthService()),
        ],
        child: const MaterialApp(
          home: LoginScreen(),
        ),
      ),
    );

    expect(find.text('Curation'), findsOneWidget);
    expect(find.text('나만의 서재를 시작하세요'), findsOneWidget);
    expect(find.text('카카오로 시작하기'), findsOneWidget);
    expect(find.text('Google로 시작하기'), findsOneWidget);
  });
}
