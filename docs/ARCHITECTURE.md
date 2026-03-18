# Architecture — 프로젝트 구조 설계

> 기술 스택 제안 상태 (개발자 리뷰 필요). 이 문서를 기반으로 리뷰 후 실제 프로젝트 셋업.

---

## 1. 전체 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                        Client (Flutter)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐   │
│  │Onboarding│ │Bookshelf │ │  Search  │ │   Feedback    │   │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                     Supabase (BaaS)                           │
│                                                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐   │
│  │   Auth   │ │PostgreSQL│ │ Storage  │ │Edge Functions │   │
│  │          │ │+pgvector │ │(표지 등) │ │  (webhooks)   │   │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
   ┌────────────┐ ┌──────────┐  ┌──────────────┐
   │ 카카오 API │ │알라딘 API│  │ 추천 서버     │
   │(메인 검색) │ │(베스트셀러│  │ (FastAPI)     │
   │            │ │ +보완)   │  │ Phase 3 ~    │
   └────────────┘ └──────────┘  └──────┬───────┘
                                       │
                                       ▼
                                ┌──────────────┐
                                │ OpenAI API   │
                                │ (Embedding)  │
                                └──────────────┘
```

### 데이터 흐름

```
[MVP - Phase 1]
유저 → Flutter 앱 → Supabase Auth (로그인)
유저 → 책 검색 → 카카오 책 검색 API (메인) → 결과 표시
유저 → 책 등록 → Supabase DB (user_books)
유저 → 피드백 입력 → Supabase DB (feedbacks)

[Phase 2 - 취향 프로필]
Supabase DB (feedbacks) → Edge Function → OpenAI Embedding
→ 취향 벡터 저장 (user_taste_vectors)
→ 취향 요약 생성 → 앱에 표시

[Phase 3 - 추천]
추천 서버 (FastAPI) ← Supabase DB (벡터 데이터)
→ 코사인 유사도 계산
→ 추천 결과 → 앱에 표시
```

---

## 2. Flutter 앱 디렉토리 구조

Feature-first 구조 채택. 기능별로 독립적인 폴더를 가지되, 공통 모듈은 `core/`에서 관리.

```
lib/
├── main.dart                    # 앱 진입점
├── app.dart                     # MaterialApp, 라우팅, 테마 설정
│
├── core/                        # 공통 모듈
│   ├── models/                  # 데이터 모델
│   │   ├── book.dart            # 책 정보 (제목, 저자, ISBN, 표지URL, 페이지수 등)
│   │   ├── user_book.dart       # 유저-책 관계 (상태: 읽음/읽는중/읽고싶은)
│   │   ├── feedback.dart        # 피드백 (카테고리, 자유텍스트, 긍정/부정)
│   │   └── user_profile.dart    # 유저 프로필
│   │
│   ├── services/                # 외부 서비스 연동
│   │   ├── supabase_service.dart    # Supabase 클라이언트
│   │   ├── book_search_service.dart # 알라딘 + Google Books API
│   │   └── auth_service.dart        # 인증 로직
│   │
│   ├── theme/                   # 디자인 시스템
│   │   ├── app_theme.dart       # 테마 정의 (warm wood tones, 폰트 등)
│   │   ├── app_colors.dart      # 컬러 팔레트
│   │   └── app_typography.dart  # 타이포그래피
│   │
│   ├── widgets/                 # 재사용 위젯
│   │   ├── book_spine.dart      # 책등 위젯 (색상 추출, 세로 텍스트)
│   │   ├── bookshelf_row.dart   # 서재 선반 한 줄
│   │   └── animated_book_add.dart # 책 꽂히는 애니메이션
│   │
│   └── utils/                   # 유틸리티
│       ├── color_extractor.dart # 표지 이미지에서 dominant color 추출
│       └── constants.dart       # 상수값
│
├── features/                    # 기능별 모듈
│   ├── onboarding/              # 온보딩
│   │   ├── screens/
│   │   │   ├── welcome_screen.dart          # 웰컴 화면
│   │   │   ├── book_selection_screen.dart    # 베스트셀러 선택 (탭탭탭)
│   │   │   ├── first_feedback_screen.dart    # 첫 피드백
│   │   │   └── bookshelf_ready_screen.dart   # "서재가 시작됐어요!"
│   │   └── widgets/
│   │       └── book_grid_selector.dart       # 책 표지 그리드 선택 위젯
│   │
│   ├── bookshelf/               # 서재 (메인 화면)
│   │   ├── screens/
│   │   │   └── bookshelf_screen.dart         # 서재 메인
│   │   ├── widgets/
│   │   │   ├── bookshelf_view.dart           # 서재 전체 뷰 (선반들)
│   │   │   ├── milestone_background.dart     # 마일스톤별 배경
│   │   │   └── empty_shelf.dart              # 빈 선반 (CTA 포함)
│   │   └── providers/
│   │       └── bookshelf_provider.dart       # 서재 상태 관리
│   │
│   ├── search/                  # 책 검색
│   │   ├── screens/
│   │   │   └── book_search_screen.dart       # 검색 화면
│   │   └── widgets/
│   │       └── book_search_result.dart       # 검색 결과 카드
│   │
│   ├── feedback/                # 피드백 입력
│   │   ├── screens/
│   │   │   └── feedback_screen.dart          # 피드백 입력 화면
│   │   └── widgets/
│   │       ├── category_selector.dart        # 카테고리 선택 (캐릭터/문체/...)
│   │       └── feedback_text_input.dart      # 자유 텍스트 입력
│   │
│   ├── book_detail/             # 책 상세
│   │   └── screens/
│   │       └── book_detail_screen.dart       # 책 상세 + 내 피드백 보기
│   │
│   └── profile/                 # 프로필
│       └── screens/
│           └── profile_screen.dart           # 프로필 + 통계
│
└── routing/                     # 라우팅
    └── app_router.dart          # GoRouter 설정
```

### 상태 관리

| 방식 | 용도 |
|------|------|
| **Riverpod** (제안) | 전역 상태 관리. 서재 데이터, 인증 상태 등 |
| **setState** | 화면 내 로컬 상태 (애니메이션, 폼 입력 등) |

> 상태 관리 라이브러리는 개발자 선호에 따라 변경 가능 (Provider, Bloc 등)

---

## 3. Supabase DB 스키마

### ERD

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────┐
│    users     │     │    user_books     │     │    books     │
├──────────────┤     ├───────────────────┤     ├──────────────┤
│ id (PK)      │──┐  │ id (PK)           │  ┌──│ id (PK)      │
│ email        │  └─▶│ user_id (FK)      │  │  │ isbn         │
│ nickname     │     │ book_id (FK)      │◀─┘  │ title        │
│ created_at   │     │ status            │     │ author       │
│ avatar_url   │     │ created_at        │     │ publisher    │
└──────────────┘     │ updated_at        │     │ cover_url    │
                     └────────┬──────────┘     │ page_count   │
                              │                │ description  │
                              │                │ genre        │
                     ┌────────▼──────────┐     │ source       │
                     │    feedbacks      │     │ source_id    │
                     ├───────────────────┤     │ created_at   │
                     │ id (PK)           │     └──────────────┘
                     │ user_book_id (FK) │
                     │ category          │     ┌──────────────────┐
                     │ sentiment         │     │ book_embeddings  │
                     │ free_text         │     ├──────────────────┤
                     │ created_at        │     │ id (PK)          │
                     └───────────────────┘     │ book_id (FK)     │
                                               │ embedding (vector)│
                     ┌───────────────────┐     │ created_at       │
                     │user_taste_vectors │     └──────────────────┘
                     ├───────────────────┤
                     │ id (PK)           │
                     │ user_id (FK)      │
                     │ cluster_label     │
                     │ vector (vector)   │
                     │ updated_at        │
                     └───────────────────┘
```

### 테이블 상세

#### `users`
Supabase Auth와 연동. 추가 프로필 정보 저장.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | Supabase Auth uid |
| email | text | 이메일 |
| nickname | text | 닉네임 |
| avatar_url | text | 프로필 이미지 |
| created_at | timestamptz | 가입일 |

#### `books`
알라딘/Google Books에서 가져온 책 정보. 앱 내에서 한 번 검색된 책은 캐싱.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | 내부 ID |
| isbn | text (unique) | ISBN |
| title | text | 제목 |
| author | text | 저자 |
| publisher | text | 출판사 |
| cover_url | text | 표지 이미지 URL |
| page_count | int | 페이지 수 (책등 너비 계산용) |
| description | text | 줄거리/설명 |
| genre | text | 장르 |
| source | text | 'aladin' or 'google_books' |
| source_id | text | 외부 API의 고유 ID |
| created_at | timestamptz | 등록일 |

#### `user_books`
유저의 서재. 책과 유저의 N:M 관계.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_id | uuid (FK → users) | |
| book_id | uuid (FK → books) | |
| status | text | 'read', 'reading', 'want_to_read' |
| created_at | timestamptz | 등록일 |
| updated_at | timestamptz | 상태 변경일 |

> unique constraint: (user_id, book_id)

#### `feedbacks`
책에 대한 유저 피드백. 하나의 user_book에 여러 피드백 가능.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_book_id | uuid (FK → user_books) | |
| category | text | 'character', 'writing_style', 'worldbuilding', 'plot', 'message', 'atmosphere' |
| sentiment | text | 'positive' or 'negative' |
| free_text | text | 자유 텍스트 |
| created_at | timestamptz | |

#### `book_embeddings` (Phase 2~3)
책의 벡터 표현. 줄거리 + 리뷰 + 장르 기반.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| book_id | uuid (FK → books) | |
| embedding | vector(1536) | OpenAI embedding 벡터 |
| created_at | timestamptz | |

#### `user_taste_vectors` (Phase 2~3)
유저의 취향 벡터. 클러스터별로 분리 저장.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_id | uuid (FK → users) | |
| cluster_label | text | 취향 축 라벨 (e.g., '캐릭터 성장', 'SF 세계관') |
| vector | vector(1536) | 취향 벡터 |
| updated_at | timestamptz | |

### RLS (Row Level Security)

```sql
-- users: 본인 데이터만 접근
-- user_books: 본인 서재만 접근
-- feedbacks: 본인 피드백만 접근
-- books: 모든 유저 읽기 가능 (공유 데이터)
```

---

## 4. API 설계

### 4-1. Supabase 직접 호출 (클라이언트 → Supabase)

MVP에서는 Supabase 클라이언트 SDK로 직접 DB 호출. 별도 API 서버 불필요.

#### 인증

| 동작 | 방식 |
|------|------|
| 회원가입/로그인 | Supabase Auth (이메일 or 소셜 로그인) |
| 세션 관리 | Supabase SDK 자동 처리 |

#### 책 검색 (외부 API)

| API | 용도 | 한도 | 비고 |
|-----|------|------|------|
| **카카오 책 검색 API** (메인) | 유저 실시간 검색 | 넉넉 | 인증 간편, 표지 양호, description 비교적 풍부 |
| **알라딘 API** (보완) | 베스트셀러 리스트(온보딩용), 한국 도서 보완 | 일 5,000회 | 배치로 인기 도서 사전 로드 |

> - 유저의 실시간 검색은 카카오 API로 처리
> - 알라딘은 일 5,000회 한도 → 매일 배치로 베스트셀러/신간 데이터를 사전 로드하여 `books` 테이블에 캐싱
> - 검색 결과 중 유저가 선택한 책만 `books` 테이블에 저장

#### 서재 CRUD

| 동작 | Supabase 호출 | 설명 |
|------|--------------|------|
| 서재 조회 | `user_books.select('*, books(*)')` | 내 서재의 모든 책 + 책 정보 |
| 책 추가 | `books.upsert()` → `user_books.insert()` | 책 캐싱 후 서재에 추가 |
| 상태 변경 | `user_books.update({ status })` | 읽음/읽는중/읽고싶은 |
| 책 제거 | `user_books.delete()` | 서재에서 제거 |

#### 피드백 CRUD

| 동작 | Supabase 호출 | 설명 |
|------|--------------|------|
| 피드백 조회 | `feedbacks.select().eq('user_book_id', id)` | 특정 책에 대한 내 피드백 |
| 피드백 추가 | `feedbacks.insert()` | 새 피드백 |
| 피드백 수정 | `feedbacks.update()` | 피드백 수정 |
| 피드백 삭제 | `feedbacks.delete()` | 피드백 삭제 |

### 4-2. 추천 서버 API (Phase 3)

FastAPI 서버. Supabase DB에서 벡터 데이터를 읽어 추천 계산.

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `POST /embeddings/book/{book_id}` | POST | 책 임베딩 생성/갱신 |
| `POST /embeddings/user/{user_id}` | POST | 유저 취향 벡터 갱신 |
| `GET /recommendations/{user_id}` | GET | 추천 도서 리스트 |
| `GET /taste-profile/{user_id}` | GET | 취향 요약 (Phase 2) |

---

## 5. 추천 서버 구조 (Phase 3 대비)

```
recommendation-server/
├── main.py                  # FastAPI 앱 진입점
├── requirements.txt
│
├── api/
│   ├── routes/
│   │   ├── embeddings.py    # 임베딩 생성 엔드포인트
│   │   ├── recommendations.py # 추천 엔드포인트
│   │   └── taste_profile.py  # 취향 프로필 엔드포인트
│   └── dependencies.py      # DB 연결, OpenAI 클라이언트
│
├── services/
│   ├── embedding_service.py  # OpenAI embedding 호출
│   ├── vector_service.py     # 벡터 저장/조회 (pgvector)
│   ├── clustering_service.py # 취향 클러스터링
│   └── recommendation_service.py # 코사인 유사도 기반 추천
│
└── models/
    └── schemas.py            # Pydantic 모델
```

### 임베딩 파이프라인

```
[책 임베딩]
책 등록 시 (또는 배치)
→ books 테이블에서 description + genre + author 조합
→ OpenAI text-embedding-3-small API 호출
→ 1536차원 벡터 반환
→ book_embeddings 테이블에 저장

[유저 취향 벡터]
피드백 n개 이상 쌓이면 트리거
→ feedbacks 테이블에서 유저의 모든 피드백 조회
→ 각 피드백 텍스트를 embedding
→ 클러스터링 (K-Means 또는 DBSCAN)
→ 클러스터별 centroid = 취향 벡터
→ user_taste_vectors 테이블에 저장

[추천]
→ user_taste_vectors와 book_embeddings 간 코사인 유사도 계산
→ 유저가 이미 읽은 책 제외
→ 상위 N개 반환
```

---

## 6. 서재 UI 기술 레퍼런스

### Flutter 패키지

| 패키지 | 용도 | 링크 |
|--------|------|------|
| `palette_generator` | 표지 이미지에서 dominant color 추출 → 책등 색상 자동 생성 | [pub.dev](https://pub.dev/packages/palette_generator_master) |
| `flutter_animate` | 책 꽂히는 모션, 마일스톤 celebration 등 커스텀 애니메이션 | [pub.dev](https://pub.dev/packages/flutter_animate) |

### 디자인 레퍼런스

| 리소스 | 설명 | 링크 |
|--------|------|------|
| Library App UI Kit (Figma, 무료) | 모바일 도서관 앱 UI 킷 | [Figma Community](https://www.figma.com/community/file/1366844221608186771) |
| Shelves App (Behance) | 서재 앱 풀 케이스 스터디 | [Behance](https://www.behance.net/gallery/107971283/Shelves-Book-Library-App-UIUX-Design) |
| The Bookshelf (Behance) | 서재 UI/UX 디자인 컨셉 | [Behance](https://www.behance.net/gallery/78500749/The-Bookshelf-UIUX-Design) |
| 60fps.design | 모바일 애니메이션 인스피레이션 | [60fps.design](https://60fps.design/) |
| Mobbin | 실제 앱 UI/UX 플로우 스크린샷 DB | [mobbin.com](https://mobbin.com/) |

### 마이크로 인터랙션 원칙

- 애니메이션 타이밍: **300~500ms** (반응성 + 만족감의 균형)
- 작은 성취 (책 1권 추가): subtle한 꽂히는 모션
- 큰 마일스톤 (10권, 50권 등): celebration 애니메이션 + 서재 배경 전환

---

## 7. MVP에서 필요한 것 / 나중에 할 것 정리

| 구분 | 포함 | 미포함 (Phase 2~3) |
|------|------|-------------------|
| **인증** | 이메일/소셜 로그인 | - |
| **책 검색** | 카카오 (메인) + 알라딘 (보완/배치) | - |
| **서재** | 책 등록/삭제, 상태 관리, 서재 UI | 마일스톤 배경 진화 (있으면 좋지만 후순위) |
| **피드백** | 카테고리 선택 + 자유 텍스트 | - |
| **온보딩** | 책 선택 + 첫 피드백 | - |
| **추천** | ❌ | 벡터 임베딩, 추천 서버 전체 |
| **취향 프로필** | ❌ | LLM 분석, 요약 생성 |
| **알림** | ❌ | 추천 알림 |

---

## 8. 개발자 리뷰 체크리스트

- [ ] 기술 스택 동의 여부 (Flutter, Supabase, Riverpod 등)
- [ ] DB 스키마 리뷰 (테이블 구조, 인덱스, RLS)
- [ ] Feature-first 디렉토리 구조 괜찮은지
- [ ] 상태 관리 라이브러리 선호 (Riverpod vs Bloc vs Provider)
- [ ] 카카오 책 검색 API + 알라딘 API 사용 조건 확인
- [ ] 소셜 로그인 범위 (Google, Apple, Kakao 등)
- [ ] 추천 서버를 별도 서버로 둘지, Supabase Edge Function으로 할지
