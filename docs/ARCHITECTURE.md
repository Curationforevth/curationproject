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
└──────────┬───────────┬──────────────────────────────────────┘
           │           │
   ┌───────┘           └──────────────┐
   ▼                                  ▼
┌─────────────────┐    ┌──────────────────────────────────────┐
│   외부 책 API    │    │     내부 엔진 (Background Workers)    │
│ ┌─────────────┐ │    │                                      │
│ │카카오 API   │ │    │  초기: PM이 Claude Code로 수동 실행   │
│ │(메인 검색)  │ │    │  성장기: 서버 자동화                  │
│ ├─────────────┤ │    │                                      │
│ │알라딘 API   │ │    │  ┌────────────┐  ┌────────────────┐  │
│ │(배치 수집)  │ │    │  │ 책 메타데이터│  │ 임베딩 생성    │  │
│ └─────────────┘ │    │  │ 강화 (AI)  │  │ & 벡터 최적화  │  │
└─────────────────┘    │  └────────────┘  └────────────────┘  │
                       │  ┌────────────┐  ┌────────────────┐  │
                       │  │ 취향 벡터  │  │ 취향 요약 &    │  │
                       │  │ 갱신       │  │ 추천 사유 (AI) │  │
                       │  └────────────┘  └────────────────┘  │
                       └──────────────────┬───────────────────┘
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                       ┌──────────┐ ┌──────────┐ ┌──────────┐
                       │Claude API│ │OpenAI API│ │추천 서버  │
                       │(메타분석 │ │(Embedding│ │(FastAPI) │
                       │ 요약생성)│ │ 벡터생성)│ │Phase 3~  │
                       └──────────┘ └──────────┘ └──────────┘
```

### 데이터 흐름

```
[MVP - Phase 1]
유저 → Flutter 앱 → Supabase Auth (로그인)
유저 → 책 검색 → 카카오 책 검색 API → 결과 표시 → 선택 시 books 테이블에 캐싱
유저 → 책 등록 → Supabase DB (user_books)
유저 → 피드백 입력 → Supabase DB (feedbacks)

[MVP - Phase 1, 백그라운드]
PM → Claude Code로 수동 실행:
  → 알라딘 배치 수집 (베스트셀러/신간 → books 테이블)
  → 책 메타데이터 강화 (짧은 description → AI 분석 → 풍부한 텍스트)
  → 임베딩 생성 (강화된 텍스트 → 벡터 → book_embeddings 테이블)

[Phase 2 - 취향 프로필]
PM → Claude Code로 수동 실행:
  → 유저 피드백 → 임베딩 → 클러스터링 → 취향 벡터 (user_taste_vectors)
  → 취향 요약 생성 → 앱에 표시

[Phase 3 - 추천 + 자동화]
내부 엔진 자동화 (Claude API + OpenAI API)
추천 서버 (FastAPI) ← Supabase DB (벡터 데이터)
→ 코사인 유사도 계산 → 추천 결과 + 추천 사유 → 앱에 표시
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
| 회원가입/로그인 | Supabase Auth — 카카오, Google, Apple Sign In |
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

## 5. 내부 엔진 (Background Workers)

유저에게는 보이지 않지만, 백그라운드에서 벡터를 생성/갱신/최적화하는 내부 엔진.

### 2단계 운영 전략

| 단계 | 방식 | 비용 | 전환 시점 |
|------|------|------|-----------|
| **초기 (수동)** | PM이 Claude Code로 직접 실행 | Claude Code 구독료만 (추가 비용 0) | 서비스 시작 ~ 수익 검증 전 |
| **성장기 (자동)** | 서버에서 Claude API / OpenAI API 자동 호출 | API 토큰 비용 발생 | 유저 증가 or 수익 발생 후 |

> 초기에 수익성 검증이 안 된 상태에서 API 비용을 태우지 않는다.
> 수동이지만 초기엔 유저/책 모두 적으니 충분하고, 직접 돌리면서 프로세스를 검증할 수 있다.

### 엔진 구성 요소

```
내부 엔진 (Background Workers)
  │
  ├─ 1. 알라딘 배치 수집기
  │   → 매일/매주 베스트셀러·신간 수집 → books 테이블에 저장
  │   → 초기: Claude Code로 수동 실행
  │   → 나중: Cron + Supabase Edge Function
  │
  ├─ 2. 책 메타데이터 강화기 (AI)
  │   → 알라딘/카카오의 짧은 description을 AI로 보강
  │   → "이 책의 장르, 분위기, 캐릭터 특성, 주제를 분석해줘"
  │   → 풍부한 텍스트 → 더 좋은 임베딩 벡터 생성
  │   → 초기: Claude Code로 수동 실행
  │   → 나중: Claude API 자동 호출
  │
  ├─ 3. 책 임베딩 생성기
  │   → 강화된 메타데이터 → 임베딩 벡터 생성
  │   → book_embeddings 테이블에 저장
  │   → 초기: Claude Code로 수동 실행
  │   → 나중: OpenAI Embedding API 자동 호출
  │
  ├─ 4. 유저 취향 벡터 갱신기
  │   → 피드백 쌓일 때마다 취향 벡터 재계산
  │   → 클러스터링으로 다중 취향 축 분리
  │   → 초기: 수동 트리거
  │   → 나중: 피드백 insert 이벤트 → 자동 갱신
  │
  ├─ 5. 책 벡터 강화기
  │   → 유저 피드백이 n개 이상 쌓인 책
  │   → description + 유저 피드백 종합해서 벡터 업그레이드
  │   → 책 벡터가 시간이 지날수록 정교해짐
  │
  └─ 6. 취향 요약 & 추천 사유 생성기 (AI)
      → "당신은 캐릭터 성장 서사를 좋아하는 독자예요"
      → "왕좌의 게임을 좋아하셨으니, 이 책의 ○○가 마음에 드실 거예요"
      → 초기: Claude Code로 수동 생성
      → 나중: Claude API 자동 생성
```

### 초기 수동 운영 예시

```
[주 1~2회, PM이 Claude Code로 실행]

1. "알라딘 베스트셀러 100권 가져와서 DB에 넣어줘"
2. "이번 주 새로 등록된 책 30권 메타데이터 분석해줘"
3. "분석 결과로 임베딩 텍스트 만들어줘"
4. "피드백 10개 이상 쌓인 유저들 취향 벡터 갱신해줘"

→ 결과를 Supabase DB에 저장
→ 비용: $0 (Claude Code 구독으로 해결)
```

---

## 5-1. 추천 서버 구조 (Phase 3, 자동화 전환 시)

수동 운영에서 자동으로 전환할 때의 서버 구조.

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
│   └── dependencies.py      # DB 연결, API 클라이언트
│
├── services/
│   ├── embedding_service.py      # OpenAI embedding 호출
│   ├── metadata_enricher.py      # Claude API로 책 메타데이터 강화
│   ├── vector_service.py         # 벡터 저장/조회 (pgvector)
│   ├── clustering_service.py     # 취향 클러스터링
│   ├── recommendation_service.py # 코사인 유사도 기반 추천
│   └── summary_service.py        # 취향 요약 & 추천 사유 생성
│
├── workers/
│   ├── batch_collector.py    # 알라딘 배치 수집
│   ├── book_enricher.py      # 책 메타데이터 강화 배치
│   ├── embedding_worker.py   # 임베딩 생성 배치
│   └── taste_updater.py      # 취향 벡터 갱신 배치
│
└── models/
    └── schemas.py            # Pydantic 모델
```

### 임베딩 파이프라인

```
[책 임베딩]
새 책 등록 or 배치 실행 시
→ books 테이블에서 description + genre + author
→ (AI 강화) Claude API로 메타데이터 분석 → 풍부한 텍스트 생성
→ OpenAI text-embedding-3-small API 호출
→ 1536차원 벡터 반환
→ book_embeddings 테이블에 저장

[책 벡터 강화]
유저 피드백 n개 이상 쌓인 책
→ 기존 description + 유저들의 피드백 텍스트 종합
→ 재임베딩 → 벡터 업데이트
→ 데이터 많을수록 벡터 품질 향상

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
→ (AI 강화) Claude API로 추천 사유 생성
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
| **인증** | 카카오 + Google + Apple 소셜 로그인 | - |
| **책 검색** | 카카오 (메인) + 알라딘 (보완/배치) | - |
| **서재** | 책 등록/삭제, 상태 관리, 서재 UI | 마일스톤 배경 진화 (있으면 좋지만 후순위) |
| **피드백** | 카테고리 선택 + 자유 텍스트 | - |
| **온보딩** | 책 선택 + 첫 피드백 | - |
| **내부 엔진** | 알라딘 배치 수집, 메타데이터 강화 (Claude Code 수동) | 자동화 전환 (Claude API + OpenAI API) |
| **임베딩** | 책 벡터 생성 (Claude Code 수동) | 자동 갱신, 피드백 기반 강화 |
| **추천** | ❌ | 추천 서버 전체, 추천 사유 생성 |
| **취향 프로필** | ❌ | 취향 요약 생성 |
| **알림** | ❌ | 추천 알림 |

---

## 8. 개발자 리뷰 체크리스트

- [ ] 기술 스택 동의 여부 (Flutter, Supabase, Riverpod 등)
- [ ] DB 스키마 리뷰 (테이블 구조, 인덱스, RLS)
- [ ] Feature-first 디렉토리 구조 괜찮은지
- [ ] 상태 관리 라이브러리 선호 (Riverpod vs Bloc vs Provider)
- [ ] 카카오 책 검색 API + 알라딘 API 사용 조건 확인
- [x] ~~소셜 로그인 범위~~ → 카카오 + Google + Apple 확정 (Apple은 App Store 정책상 필수)
- [ ] 추천 서버를 별도 서버로 둘지, Supabase Edge Function으로 할지
