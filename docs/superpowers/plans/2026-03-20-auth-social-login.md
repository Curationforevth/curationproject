# Auth 소셜 로그인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 카카오 + Google 소셜 로그인을 구현하고, 세션 기반 라우팅으로 로그인/홈 화면을 자동 전환한다.

**Architecture:** `kakao_flutter_sdk`로 카카오 토큰 획득 → Supabase `signInWithIdToken()`, Google은 Supabase OAuth 직접 사용. Riverpod `StreamProvider`로 세션 감시, GoRouter `redirect`로 자동 리다이렉트.

**Tech Stack:** Flutter, supabase_flutter, kakao_flutter_sdk_user, flutter_riverpod, go_router

**Spec:** `docs/superpowers/specs/2026-03-20-auth-social-login-design.md`

---

## 파일 구조

### 신규 생성

| 파일 | 역할 |
|------|------|
| `app/lib/features/auth/services/auth_service.dart` | 카카오/Google 인증 로직 |
| `app/lib/features/auth/providers/auth_provider.dart` | Riverpod 인증 상태 (세션 감시) |
| `app/lib/features/auth/screens/login_screen.dart` | 로그인 화면 (소셜 버튼 2개) |

### 수정

| 파일 | 변경 내용 |
|------|----------|
| `app/pubspec.yaml` | `kakao_flutter_sdk_user` 추가 |
| `app/lib/main.dart` | 카카오 SDK 초기화 추가 |
| `app/lib/routing/app_router.dart` | `/login` 라우트 + 세션 리다이렉트 + splash 상태 |
| `app/lib/app.dart` | GoRouter를 Riverpod provider로 전환 |
| `app/.env.example` | `KAKAO_NATIVE_APP_KEY` 추가 |
| `app/ios/Runner/Info.plist` | 카카오 URL scheme + Supabase 딥링크 |
| `app/android/app/src/main/AndroidManifest.xml` | 카카오 + Supabase 딥링크 intent-filter |
| `app/test/widget_test.dart` | 로그인 화면 테스트로 변경 |

---

## Task 1: 패키지 추가 + 카카오 SDK 초기화

**Files:**
- Modify: `app/pubspec.yaml`
- Modify: `app/lib/main.dart`
- Modify: `app/.env.example`

- [ ] **Step 1: pubspec.yaml에 kakao_flutter_sdk_user 추가**

`app/pubspec.yaml`의 `dependencies:`에 추가:
```yaml
  kakao_flutter_sdk_user: ^1.9.0
```

- [ ] **Step 2: flutter pub get**

Run: `cd app && flutter pub get`
Expected: 성공

- [ ] **Step 3: .env.example 업데이트**

`app/.env.example`에 추가:
```
KAKAO_NATIVE_APP_KEY=your_kakao_native_app_key
```

- [ ] **Step 4: main.dart에 카카오 SDK 초기화 추가**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:kakao_flutter_sdk_user/kakao_flutter_sdk_user.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await dotenv.load(fileName: '.env');

  // 카카오 SDK 초기화
  KakaoSdk.init(nativeAppKey: dotenv.env['KAKAO_NATIVE_APP_KEY']!);

  await Supabase.initialize(
    url: dotenv.env['SUPABASE_URL']!,
    anonKey: dotenv.env['SUPABASE_ANON_KEY']!,
  );

  runApp(const ProviderScope(child: App()));
}
```

- [ ] **Step 5: 커밋**

```bash
git add app/pubspec.yaml app/lib/main.dart app/.env.example
git commit -m "feat: 카카오 SDK 초기화 + 패키지 추가"
```

---

## Task 2: Auth Service 구현

**Files:**
- Create: `app/lib/features/auth/services/auth_service.dart`

- [ ] **Step 1: auth_service.dart 작성**

```dart
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
      throw Exception('카카오 idToken이 null입니다. OpenID Connect가 활성화되어 있는지 확인하세요.');
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
  Stream<AuthState> get onAuthStateChange => _supabase.auth.onAuthStateChange;
}
```

- [ ] **Step 2: 커밋**

```bash
git add app/lib/features/auth/services/auth_service.dart
git commit -m "feat: AuthService (카카오 + Google 로그인)"
```

---

## Task 3: Auth Provider (Riverpod)

**Files:**
- Create: `app/lib/features/auth/providers/auth_provider.dart`

- [ ] **Step 1: auth_provider.dart 작성**

```dart
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../services/auth_service.dart';

/// AuthService 싱글톤
final authServiceProvider = Provider<AuthService>((ref) => AuthService());

/// 인증 상태 스트림
final authStateProvider = StreamProvider<AuthState>((ref) {
  return ref.watch(authServiceProvider).onAuthStateChange;
});

/// GoRouter refresh용 Listenable
class AuthNotifier extends ChangeNotifier {
  late final StreamSubscription<AuthState> _subscription;

  AuthNotifier() {
    _subscription = Supabase.instance.client.auth.onAuthStateChange.listen((_) {
      notifyListeners();
    });
  }

  @override
  void dispose() {
    _subscription.cancel();
    super.dispose();
  }
}
```

- [ ] **Step 2: 커밋**

```bash
git add app/lib/features/auth/providers/auth_provider.dart
git commit -m "feat: Riverpod 인증 상태 provider + AuthNotifier"
```

---

## Task 4: 로그인 화면

**Files:**
- Create: `app/lib/features/auth/screens/login_screen.dart`

- [ ] **Step 1: login_screen.dart 작성**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/auth_provider.dart';

class LoginScreen extends ConsumerWidget {
  const LoginScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authService = ref.watch(authServiceProvider);

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  'Curation',
                  style: Theme.of(context).textTheme.headlineLarge,
                ),
                const SizedBox(height: 8),
                Text(
                  '나만의 서재를 시작하세요',
                  style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                        color: Colors.grey,
                      ),
                ),
                const SizedBox(height: 48),

                // 카카오 로그인
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton(
                    onPressed: () => _signInWithKakao(context, authService),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFFFEE500),
                      foregroundColor: Colors.black87,
                    ),
                    child: const Text('카카오로 시작하기'),
                  ),
                ),
                const SizedBox(height: 12),

                // Google 로그인
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: OutlinedButton(
                    onPressed: () => _signInWithGoogle(context, authService),
                    child: const Text('Google로 시작하기'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _signInWithKakao(BuildContext context, AuthService authService) async {
    try {
      await authService.signInWithKakao();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('카카오 로그인 실패: $e')),
        );
      }
    }
  }

  Future<void> _signInWithGoogle(BuildContext context, AuthService authService) async {
    try {
      await authService.signInWithGoogle();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Google 로그인 실패: $e')),
        );
      }
    }
  }
}
```

- [ ] **Step 2: 커밋**

```bash
git add app/lib/features/auth/screens/login_screen.dart
git commit -m "feat: 로그인 화면 (카카오 + Google 버튼)"
```

---

## Task 5: 라우팅 + 세션 리다이렉트

**Files:**
- Modify: `app/lib/routing/app_router.dart`
- Modify: `app/lib/app.dart`

- [ ] **Step 1: app_router.dart 리팩터**

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../features/auth/providers/auth_provider.dart';
import '../features/auth/screens/login_screen.dart';

GoRouter createRouter(AuthNotifier authNotifier) {
  return GoRouter(
    initialLocation: '/',
    refreshListenable: authNotifier,
    redirect: (context, state) {
      final session = Supabase.instance.client.auth.currentSession;
      final isLoginRoute = state.matchedLocation == '/login';

      if (session == null && !isLoginRoute) return '/login';
      if (session != null && isLoginRoute) return '/';
      return null;
    },
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => const HomePage(),
      ),
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginScreen(),
      ),
    ],
  );
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Curation')),
      body: Center(
        child: ElevatedButton(
          onPressed: () => Supabase.instance.client.auth.signOut(),
          child: const Text('로그아웃'),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: app.dart 업데이트 — GoRouter를 Riverpod으로 관리**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/theme/app_theme.dart';
import 'features/auth/providers/auth_provider.dart';
import 'routing/app_router.dart';

final routerProvider = Provider<GoRouter>((ref) {
  // 이 import는 go_router 필요
  throw UnimplementedError();
});

class App extends ConsumerStatefulWidget {
  const App({super.key});

  @override
  ConsumerState<App> createState() => _AppState();
}

class _AppState extends ConsumerState<App> {
  late final AuthNotifier _authNotifier;
  late final GoRouter _router;

  @override
  void initState() {
    super.initState();
    _authNotifier = AuthNotifier();
    _router = createRouter(_authNotifier);
  }

  @override
  void dispose() {
    _authNotifier.dispose();
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Curation',
      theme: AppTheme.light,
      routerConfig: _router,
      debugShowCheckedModeBanner: false,
    );
  }
}
```

- [ ] **Step 3: import 정리 — go_router import 추가**

`app/lib/app.dart` 상단에:
```dart
import 'package:go_router/go_router.dart';
```

그리고 사용하지 않는 `routerProvider`는 제거 (위 코드에서 실수로 포함됨).

최종 `app.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'core/theme/app_theme.dart';
import 'features/auth/providers/auth_provider.dart';
import 'routing/app_router.dart';

class App extends ConsumerStatefulWidget {
  const App({super.key});

  @override
  ConsumerState<App> createState() => _AppState();
}

class _AppState extends ConsumerState<App> {
  late final AuthNotifier _authNotifier;
  late final GoRouter _router;

  @override
  void initState() {
    super.initState();
    _authNotifier = AuthNotifier();
    _router = createRouter(_authNotifier);
  }

  @override
  void dispose() {
    _authNotifier.dispose();
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Curation',
      theme: AppTheme.light,
      routerConfig: _router,
      debugShowCheckedModeBanner: false,
    );
  }
}
```

- [ ] **Step 4: 커밋**

```bash
git add app/lib/routing/app_router.dart app/lib/app.dart
git commit -m "feat: 세션 기반 라우팅 (로그인/홈 자동 전환)"
```

---

## Task 6: 플랫폼 설정 (iOS + Android 딥링크)

**Files:**
- Modify: `app/ios/Runner/Info.plist`
- Modify: `app/android/app/src/main/AndroidManifest.xml`

- [ ] **Step 1: iOS Info.plist — URL scheme 추가**

`app/ios/Runner/Info.plist`의 `<dict>` 안에 추가:

```xml
<!-- 카카오 SDK -->
<key>LSApplicationQueriesSchemes</key>
<array>
    <string>kakaokompassauth</string>
    <string>kakaolink</string>
</array>
<key>CFBundleURLTypes</key>
<array>
    <dict>
        <key>CFBundleURLSchemes</key>
        <array>
            <string>kakao9104b92af02f9b1ee8d93d3163904636</string>
        </array>
    </dict>
    <dict>
        <key>CFBundleURLSchemes</key>
        <array>
            <string>io.supabase.curation</string>
        </array>
    </dict>
</array>
```

- [ ] **Step 2: Android AndroidManifest.xml — intent-filter 추가**

`app/android/app/src/main/AndroidManifest.xml`의 `<activity>` 안에 추가:

```xml
<!-- 카카오 로그인 리다이렉트 -->
<intent-filter>
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="kakao9104b92af02f9b1ee8d93d3163904636" android:host="oauth" />
</intent-filter>

<!-- Supabase OAuth 리다이렉트 -->
<intent-filter>
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="io.supabase.curation" android:host="login-callback" />
</intent-filter>
```

- [ ] **Step 3: 커밋**

```bash
git add app/ios/Runner/Info.plist app/android/app/src/main/AndroidManifest.xml
git commit -m "feat: iOS/Android 딥링크 설정 (카카오 + Supabase OAuth)"
```

---

## Task 7: 테스트 + 빌드 확인

**Files:**
- Modify: `app/test/widget_test.dart`

- [ ] **Step 1: 위젯 테스트 업데이트**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/features/auth/screens/login_screen.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

void main() {
  testWidgets('LoginScreen displays social login buttons', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(
          home: LoginScreen(),
        ),
      ),
    );

    expect(find.text('Curation'), findsOneWidget);
    expect(find.text('카카오로 시작하기'), findsOneWidget);
    expect(find.text('Google로 시작하기'), findsOneWidget);
  });
}
```

- [ ] **Step 2: 테스트 실행**

Run: `cd app && flutter test`
Expected: 1 passed

- [ ] **Step 3: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 4: 빌드 확인**

Run: `cd app && flutter build web`
Expected: 빌드 성공

- [ ] **Step 5: 최종 커밋 + 푸시**

```bash
git add app/test/widget_test.dart
git commit -m "test: 로그인 화면 위젯 테스트"
git push origin main
```

---

## 완료 기준

1. 로그인 화면에 카카오/Google 버튼 2개 표시
2. 세션 없으면 → 로그인 화면, 세션 있으면 → 홈 화면
3. 로그아웃 → 로그인 화면으로 이동
4. `flutter test` 통과
5. `flutter analyze` 0 issues

## 구현 제외

| 항목 | 사유 |
|------|------|
| Apple 로그인 | Apple Developer Program 미가입 — 출시 전 추가 |
| 닉네임 입력 | 프로필 화면에서 나중에 |
| 신규/기존 유저 분기 | 온보딩 스펙에서 처리 |
| 실제 로그인 테스트 | 실기기/시뮬레이터에서 수동 테스트 필요 |
