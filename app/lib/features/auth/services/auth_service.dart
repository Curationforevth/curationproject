import 'package:kakao_flutter_sdk_user/kakao_flutter_sdk_user.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class AuthService {
  final SupabaseClient _supabase = Supabase.instance.client;

  /// 카카오 로그인 — KakaoTalk 앱 우선, 없으면 웹 로그인
  Future<AuthResponse> signInWithKakao() async {
    OAuthToken token;
    if (await isKakaoTalkInstalled()) {
      token = await UserApi.instance.loginWithKakaoTalk(scopes: ['openid']);
    } else {
      token = await UserApi.instance.loginWithKakaoAccount(scopes: ['openid']);
    }

    final idToken = token.idToken;
    if (idToken == null) {
      throw Exception(
        '카카오 idToken이 null입니다. OpenID Connect가 활성화되어 있는지 확인하세요.',
      );
    }

    return await _supabase.auth.signInWithIdToken(
      provider: OAuthProvider.kakao,
      idToken: idToken,
    );
  }

  /// Google 로그인 — Supabase OAuth
  Future<void> signInWithGoogle() async {
    await _supabase.auth.signInWithOAuth(
      OAuthProvider.google,
      redirectTo: 'io.supabase.curation://login-callback/',
    );
  }

  /// 로그아웃
  Future<void> signOut() async {
    await _supabase.auth.signOut();
  }

  /// 현재 세션
  Session? get currentSession => _supabase.auth.currentSession;

  /// 인증 상태 스트림
  Stream<AuthState> get onAuthStateChange =>
      _supabase.auth.onAuthStateChange;
}
