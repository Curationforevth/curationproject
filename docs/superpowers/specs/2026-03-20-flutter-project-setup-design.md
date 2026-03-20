# Flutter 프로젝트 셋업 — 설계 스펙

## 1. 개요

Curation 앱의 Flutter 프로젝트를 초기화한다. UI 없이 인프라만 구축:
- Flutter 프로젝트 생성 (`curation/app/`)
- 패키지 의존성 설정
- Feature-first 디렉토리 구조 (실제 코드가 있는 것만)
- Supabase 연결
- 라우팅 뼈대
- 기본 테마 (Material 3 + StoryGraph 스타일 방향)

## 2. 프로젝트 위치

`curation/app/` — 모노레포 구조로 백엔드(`scripts/`)와 함께 관리.

```
curation/
├── scripts/          # 배치 수집, 임베딩 (Python)
├── docs/             # 기획/설계 문서
├── supabase/         # 마이그레이션 SQL
├── tests/            # Python 테스트
├── app/              # ← Flutter 프로젝트 (신규)
│   ├── lib/
│   ├── android/
│   ├── ios/
│   ├── test/
│   └── pubspec.yaml
└── ...
```

## 3. 디렉토리 구조

빈 폴더는 만들지 않는다. 각 feature 구현 시 생성.

```
app/lib/
├── main.dart                              # 진입점: dotenv, Supabase 초기화
├── app.dart                               # MaterialApp.router + GoRouter + 테마
├── core/
│   ├── services/
│   │   └── supabase_service.dart          # Supabase 클라이언트 싱글톤
│   └── theme/
│       └── app_theme.dart                 # Material 3 기본 테마
└── routing/
    └── app_router.dart                    # GoRouter 뼈대 (홈 placeholder)
```

## 4. 패키지 의존성

### dependencies

| 패키지 | 버전 | 용도 |
|--------|------|------|
| `supabase_flutter` | latest | Supabase Auth + DB + Storage |
| `flutter_riverpod` | latest | 상태 관리 |
| `go_router` | latest | 선언적 라우팅 |
| `flutter_dotenv` | latest | .env 환경변수 로드 |

### dev_dependencies

Flutter 기본 제공 (`flutter_test`, `flutter_lints`) 사용. 추가 패키지 없음.

### 지금 안 넣는 것

- `forui` — UI 컴포넌트 라이브러리, 첫 UI feature 때 추가
- `palette_generator`, `flutter_animate` — 서재 UI 때
- `kakao_flutter_sdk` — Auth 때
- `google_sign_in`, `sign_in_with_apple` — Auth 때

## 5. 환경변수

`app/.env.example`:
```
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
```

실제 값은 `app/.env`에 저장. 루트 `.gitignore`에 `app/.env` 추가 필수.

## 6. 파일별 역할

### `main.dart`
- `flutter_dotenv`로 `.env` 로드
- `Supabase.initialize()` 호출
- `ProviderScope`로 감싸서 `runApp(const App())`

### `app.dart`
- `MaterialApp.router` 사용
- GoRouter 연결
- AppTheme 적용

### `supabase_service.dart`
- `Supabase.instance.client` 접근용 싱글톤
- 추후 Auth, DB 호출의 진입점

### `app_theme.dart`
- Material 3 활성화 (`useMaterial3: true`)
- 기본 폰트 설정
- 컬러 시스템은 비워둠 — UI 디자인 확정 후 적용
- 방향성: StoryGraph 스타일 (라이트 베이스 + 파스텔 액센트)

### `app_router.dart`
- GoRouter 인스턴스
- `/` → 홈 placeholder 화면 ("Curation" 텍스트)
- 추후 `/onboarding`, `/search`, `/book/:id` 등 추가

## 7. 선행 조건

- Flutter SDK >= 3.22.0 (stable) 설치 필요 (로컬에 미설치 상태)
- Xcode (iOS 빌드) / Android Studio (Android 빌드) 환경 확인

## 8. UI 디자인 방향 (메모)

- 레퍼런스: **StoryGraph** — 라이트 톤, 파스텔 컬러, 깔끔한 독서 앱
- 셋업에서는 테마 구조만 잡고, 실제 컬러/스타일은 UI 작업 시 결정
- warm wood tone은 올드한 느낌 → 보류 (PRODUCT_PLAN.md 원래 방향에서 의도적 전환)

## 9. 구현 제외

| 항목 | 사유 |
|------|------|
| Auth (소셜 로그인) | 별도 스펙 |
| 온보딩 플로우 | 별도 스펙 |
| 서재 UI | 별도 스펙 + UI 디자인 필요 |
| 커스텀 컬러 테마 | UI 디자인 확정 후 |

## 10. 완료 기준

1. `flutter run`으로 iOS 시뮬레이터 또는 Chrome에서 앱 실행 — "Curation" placeholder 화면 표시
2. Supabase 연결 성공 로그 출력 (초기화 에러 없음)
3. `flutter test` 기본 위젯 테스트 통과
4. `flutter analyze` 경고/에러 0건
