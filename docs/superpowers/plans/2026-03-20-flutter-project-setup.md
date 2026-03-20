# Flutter 프로젝트 셋업 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Curation 앱의 Flutter 프로젝트를 `app/` 디렉토리에 초기화하고, Supabase 연결 + 라우팅 뼈대를 구축한다.

**Architecture:** `curation/app/`에 Flutter 프로젝트를 생성하고 feature-first 구조로 최소 뼈대만 구성한다. Supabase 클라이언트 초기화, GoRouter 라우팅, Material 3 기본 테마를 설정한다. UI는 placeholder만.

**Tech Stack:** Flutter >= 3.22.0, Dart, supabase_flutter, flutter_riverpod, go_router, flutter_dotenv

**Spec:** `docs/superpowers/specs/2026-03-20-flutter-project-setup-design.md`

---

## 파일 구조

### 신규 생성

| 파일 | 역할 |
|------|------|
| `app/lib/main.dart` | 앱 진입점 — dotenv 로드, Supabase 초기화, ProviderScope |
| `app/lib/app.dart` | MaterialApp.router + GoRouter + 테마 |
| `app/lib/core/services/supabase_service.dart` | Supabase 클라이언트 접근용 |
| `app/lib/core/theme/app_theme.dart` | Material 3 기본 테마 |
| `app/lib/routing/app_router.dart` | GoRouter 뼈대 (홈 placeholder) |
| `app/.env.example` | 환경변수 템플릿 |

### 수정

| 파일 | 변경 내용 |
|------|----------|
| `.gitignore` (루트) | `app/.env` 추가 |

---

## Task 1: Flutter SDK 설치 + 환경 확인

**Files:** 없음 (환경 설정)

- [ ] **Step 1: Flutter SDK 설치**

Homebrew로 설치:
```bash
brew install --cask flutter
```

- [ ] **Step 2: Flutter 버전 확인**

Run: `flutter --version`
Expected: Flutter >= 3.22.0 (stable channel)

- [ ] **Step 3: flutter doctor 실행**

Run: `flutter doctor`
Expected: Flutter, Dart 체크 통과. Xcode/Android toolchain은 경고 있을 수 있음 (나중에 해결 가능)

- [ ] **Step 4: Chrome 빌드 확인**

Run: `flutter doctor | grep Chrome`
Expected: Chrome 설치 확인 (web 빌드용, 시뮬레이터 없이도 테스트 가능)

---

## Task 2: Flutter 프로젝트 생성 + 패키지 설정

**Files:**
- Create: `app/` (flutter create)
- Modify: `app/pubspec.yaml`
- Modify: `.gitignore` (루트)

- [ ] **Step 1: Flutter 프로젝트 생성**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
flutter create --org com.curation --project-name curation_app app
```

- [ ] **Step 2: pubspec.yaml 의존성 추가**

`app/pubspec.yaml`의 `dependencies:` 섹션에 추가:

```yaml
dependencies:
  flutter:
    sdk: flutter
  supabase_flutter: ^2.0.0
  flutter_riverpod: ^2.0.0
  go_router: ^14.0.0
  flutter_dotenv: ^5.0.0
```

- [ ] **Step 3: 패키지 설치**

Run: `cd app && flutter pub get`
Expected: 의존성 해결 성공, 에러 없음

- [ ] **Step 4: 루트 .gitignore에 app/.env 추가**

`.gitignore`에 추가:
```
app/.env
```

- [ ] **Step 5: .env.example 생성**

`app/.env.example`:
```
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
```

- [ ] **Step 6: app/.env 생성 (실제 값)**

루트 `.env` 파일에서 `SUPABASE_URL`과 `SUPABASE_ANON_KEY` 값을 복사하여 `app/.env`에 저장.

**주의:** 이 파일은 gitignore 대상. 커밋하지 않음.

- [ ] **Step 7: pubspec.yaml에 assets 등록**

`app/pubspec.yaml`의 `flutter:` 섹션에 추가:
```yaml
flutter:
  uses-material-design: true
  assets:
    - .env
```

- [ ] **Step 8: 커밋**

```bash
git add app/ .gitignore
git reset app/.env  # .env는 제외
git status  # app/.env가 staged에 없는지 확인
git commit -m "feat: Flutter 프로젝트 초기화 + 패키지 설정"
```

---

## Task 3: Supabase 서비스 + 테마

**Files:**
- Create: `app/lib/core/services/supabase_service.dart`
- Create: `app/lib/core/theme/app_theme.dart`

- [ ] **Step 1: supabase_service.dart 작성**

```dart
import 'package:supabase_flutter/supabase_flutter.dart';

class SupabaseService {
  static SupabaseClient get client => Supabase.instance.client;
}
```

- [ ] **Step 2: app_theme.dart 작성**

```dart
import 'package:flutter/material.dart';

class AppTheme {
  static ThemeData get light => ThemeData(
        useMaterial3: true,
        colorSchemeSeed: Colors.deepPurple,
        brightness: Brightness.light,
        // fontFamily: UI 디자인 확정 후 설정
      );
}
```

- [ ] **Step 3: 커밋**

```bash
git add app/lib/core/
git commit -m "feat: Supabase 서비스 + Material 3 기본 테마"
```

---

## Task 4: 라우팅 + App + Main

**Files:**
- Create: `app/lib/routing/app_router.dart`
- Create: `app/lib/app.dart`
- Modify: `app/lib/main.dart`

- [ ] **Step 1: app_router.dart 작성**

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

final goRouter = GoRouter(
  initialLocation: '/',
  routes: [
    GoRoute(
      path: '/',
      builder: (context, state) => const HomePage(),
    ),
  ],
);

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Text(
          'Curation',
          style: Theme.of(context).textTheme.headlineLarge,
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: app.dart 작성**

```dart
import 'package:flutter/material.dart';
import 'core/theme/app_theme.dart';
import 'routing/app_router.dart';

class App extends StatelessWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Curation',
      theme: AppTheme.light,
      routerConfig: goRouter,
      debugShowCheckedModeBanner: false,
    );
  }
}
```

- [ ] **Step 3: main.dart 작성**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await dotenv.load(fileName: '.env');

  await Supabase.initialize(
    url: dotenv.env['SUPABASE_URL']!,
    anonKey: dotenv.env['SUPABASE_ANON_KEY']!,
  );

  runApp(const ProviderScope(child: App()));
}
```

- [ ] **Step 4: 기본 위젯 테스트 업데이트**

`app/test/widget_test.dart`:

```dart
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
```

- [ ] **Step 5: 테스트 실행**

Run: `cd app && flutter test`
Expected: 1 test passed

- [ ] **Step 6: 커밋**

```bash
git add app/lib/ app/test/
git commit -m "feat: 라우팅 + App + Main 진입점"
```

---

## Task 5: 빌드 확인 + 정리

**Files:** 없음 (검증)

- [ ] **Step 1: flutter analyze 실행**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 2: 앱 실행 확인 (Chrome)**

Run: `cd app && flutter run -d chrome`
Expected: 브라우저에서 "Curation" 텍스트가 중앙에 표시됨

- [ ] **Step 3: 앱 종료 후 최종 푸시**

```bash
git push origin main
```

---

## 완료 기준

1. `flutter run -d chrome`으로 "Curation" placeholder 화면 표시
2. Supabase 초기화 에러 없음
3. `flutter test` 통과
4. `flutter analyze` 경고/에러 0건
