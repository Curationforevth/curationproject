# Auth 소셜 로그인 — 설계 스펙

## 1. 개요

카카오 + Google 소셜 로그인 구현. Supabase Auth 기반. Apple은 앱스토어 출시 전에 추가.

- Google: Supabase OAuth 직접 사용
- 카카오: `kakao_flutter_sdk`로 토큰 획득 → Supabase `signInWithIdToken()`
- 프로필: Supabase Auth 기본 제공 정보만 (email, avatar). 닉네임은 나중에.
- UI: 최소한 — 로고 + 소셜 버튼 3개

## 2. 인증 흐름

```
앱 시작 → Supabase 세션 확인
  ├─ 세션 있음 → 홈(서재) 화면
  └─ 세션 없음 → 로그인 화면
      ├─ [카카오 로그인] → kakao_flutter_sdk → 카카오 토큰 → Supabase signInWithIdToken
      ├─ [Google 로그인] → Supabase signInWithOAuth('google')
      └─ [Apple 로그인] → Supabase signInWithOAuth('apple')
      → 로그인 성공 → 홈 화면
```

로그아웃: `Supabase.instance.client.auth.signOut()` → 로그인 화면

## 3. 파일 구조

### 신규 생성

| 파일 | 역할 |
|------|------|
| `app/lib/features/auth/screens/login_screen.dart` | 로그인 화면 (앱 이름 + 소셜 버튼 3개) |
| `app/lib/features/auth/services/auth_service.dart` | 인증 로직. ARCHITECTURE.md의 `core/services/`가 아닌 feature-first 배치 — Auth는 feature 스코프. |
| `app/lib/features/auth/providers/auth_provider.dart` | Riverpod 인증 상태 (로그인/로그아웃/세션 감시) |

### 수정

| 파일 | 변경 |
|------|------|
| `app/lib/routing/app_router.dart` | `/login` 라우트 추가, 세션 기반 리다이렉트 |
| `app/pubspec.yaml` | `kakao_flutter_sdk_user` 추가 |
| `app/.env.example` / `app/.env` | `KAKAO_NATIVE_APP_KEY` 추가 |
| `app/lib/main.dart` | 카카오 SDK 초기화 추가 |

## 4. 패키지

| 패키지 | 용도 |
|--------|------|
| `kakao_flutter_sdk_user` | 카카오 로그인 (토큰 획득) |

Google/Apple은 `supabase_flutter`의 OAuth로 처리 — 추가 패키지 불필요.

## 5. 플랫폼별 설정 (수동 작업 필요)

### 카카오

1. **developers.kakao.com → 내 애플리케이션 → 플랫폼**
   - iOS: Bundle ID 등록
   - Android: 패키지명 + 키해시 등록
2. **카카오 로그인 활성화** → 동의항목에서 이메일, 프로필 체크
3. **OpenID Connect 활성화** → 카카오 로그인 → 고급 → OpenID Connect 활성화 (idToken 발급 필수)
4. **Native App 키** 발급 → `app/.env`에 `KAKAO_NATIVE_APP_KEY=...` 추가

### Google (Supabase)

1. **Supabase 대시보드 → Authentication → Providers → Google**
   - Google Cloud Console에서 OAuth 2.0 Client ID 생성
   - Client ID + Secret을 Supabase에 입력
   - Redirect URL: Supabase가 제공하는 URL 복사 → Google Console에 등록

### Apple (Supabase)

1. **Supabase 대시보드 → Authentication → Providers → Apple**
   - Apple Developer Console에서 Service ID 생성
   - Redirect URL 등록
   - Key 파일 업로드

> 이 설정들은 코드 구현과 별도로 진행. 플랜에서 "수동 설정" Task로 분리.

## 6. auth_service.dart 핵심 로직

```dart
// Google
await supabase.auth.signInWithOAuth(OAuthProvider.google);

// Apple
await supabase.auth.signInWithOAuth(OAuthProvider.apple);

// 카카오 — KakaoTalk 앱 우선, 없으면 웹 로그인
// OpenID Connect 활성화 필수 (idToken 발급)
Future<String> kakaoLogin() async {
  OAuthToken token;
  if (await isKakaoTalkInstalled()) {
    token = await UserApi.instance.loginWithKakaoTalk(scopes: ['openid']);
  } else {
    token = await UserApi.instance.loginWithKakaoAccount(scopes: ['openid']);
  }
  return token.idToken!;  // OIDC 활성화 시 idToken 존재
}

// 사용
final idToken = await kakaoLogin();
await supabase.auth.signInWithIdToken(
  provider: OAuthProvider.kakao,
  idToken: idToken,
);
```

## 7. auth_provider.dart

```dart
// Riverpod StreamProvider로 세션 감시
final authStateProvider = StreamProvider<AuthState>((ref) {
  return supabase.auth.onAuthStateChange;
});
```

라우터에서 이 provider를 watch하여 로그인/로그아웃 시 자동 리다이렉트.

## 8. 로그인 화면 (최소 UI)

- 상단: "Curation" 텍스트 (앱 이름)
- 중앙: 소셜 로그인 버튼 3개 (카카오 노란색, Google 흰색, Apple 검정)
- 하단: 서비스 약관 링크 (placeholder)

디자인은 나중에 개선. 기능 동작 확인용.

## 9. 라우팅 변경

```dart
GoRouter(
  redirect: (context, state) {
    final session = supabase.auth.currentSession;
    final isLoginRoute = state.matchedLocation == '/login';

    if (session == null && !isLoginRoute) return '/login';
    if (session != null && isLoginRoute) return '/';
    return null;
  },
  routes: [
    GoRoute(path: '/', builder: ... HomePage),
    GoRoute(path: '/login', builder: ... LoginScreen),
  ],
);
```

## 10. 완료 기준

1. 카카오 로그인 → Supabase 세션 생성 → 홈 화면 이동
2. Google 로그인 → 동일
3. Apple 로그인 → 동일
4. 앱 재시작 시 세션 유지 → 로그인 화면 스킵
5. 로그아웃 → 로그인 화면으로 이동
6. `flutter test` 통과
7. `flutter analyze` 0 issues

## 11. 구현 제외

| 항목 | 사유 |
|------|------|
| 닉네임 입력 | 프로필 화면에서 나중에 |
| 신규/기존 유저 분기 | 온보딩 스펙에서 처리 |
| 회원 탈퇴 | 나중에 |
| 비밀번호 로그인 | 소셜만 지원 |
