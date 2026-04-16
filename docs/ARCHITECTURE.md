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
유저 → 책 등록 → palette_generator로 표지 dominant color 추출 → books.dominant_colors 저장
  → LLM으로 메타데이터 분석 → mood_tags + spine_font 자동 배정 → books 테이블 업데이트
유저 → 책 등록 → Supabase DB (user_books)
유저 → 피드백 입력 → Supabase DB (user_books.rating, emotion_tags, review_text)

[MVP - Phase 1, 백그라운드 — GitHub Actions 자동화]

매일 KST 03:00 (daily-pipeline.yml — 3개 병렬 job + 1개 순차 job):
  [병렬] discovery:
    → 정보나루 대출 순위 수집 (data4library_discovery_collector.py)
    → 장르 보강: genre NULL 책을 알라딘 API로 보강 (backfill_genre.py)
  [병렬] collect:
    → 알라딘 배치 수집 (smart_batch_collector.py → books 테이블)
    → 색상 추출 + 폰트 배정 (batch_enricher.py)
    → Tier 1 임베딩 생성 (tier1_embedder.py)
  [병렬] enrich:
    → v3 벡터 생성 (generate_book_v3_vectors.py → desc_embedding + l1/l2 장르)
    → reason 추출 (v3_reason_extract.py → book_love_reasons)
    → Tier 2 임베딩 (tier2_embedder.py)
  [순차] build-and-recompute (위 3개 완료 후):
    → 추천 인덱스 빌드 (build_index.py --incremental)
    → 취향 벡터 재계산 (taste_recomputer.py)

6시간마다 (daily-scrape.yml):
  → YES24 상세 수집 240권/회 (책소개/출판사리뷰/책속으로 → rich_description)

실시간 (유저 피드백 제출 시):
  → RPC: recompute_taste_vector_immediate → 즉시 취향 벡터 갱신
  → RPC: match_books_by_similarity → book-to-book 유사도
  → RPC: recommend_books_for_user → 취향 기반 추천 (v1)
  → (Phase 2+) LLM으로 "좋아하는 이유" 추출 → user_taste_reasons 저장

[Phase 2 - 취향 프로필 + 추천 고도화]
배치에서 자동 처리:
  → K-means 클러스터링 → 클러스터별 LLM 라벨 생성
  → LLM 취향 요약 ("당신은 잔잔한 캐릭터 성장 서사를 좋아하는 독자예요")
  → 추천 이유 생성 ("이 책의 서정적인 문체가 마음에 드실 거예요")
  → (선택) feedbacks 테이블 활성화하여 카테고리별 상세 피드백 수집

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
│   ├── models/
│   │   ├── book.dart            # 책 정보
│   │   ├── user_book.dart       # 유저-책 관계 (상태, rating, emotion_tags, review_text)
│   │   ├── feedback.dart        # 피드백 모델 (Phase 2~3용)
│   │   ├── user_profile.dart    # 유저 프로필
│   │   ├── emotion_tag.dart     # 감성 태그 옵션
│   │   └── reflection_prompt.dart # 리뷰 도우미 질문
│   │
│   ├── services/
│   │   ├── supabase_service.dart        # Supabase 클라이언트
│   │   ├── book_search_service.dart     # 카카오 책 검색 API (페이지네이션)
│   │   └── book_registration_service.dart # 책 등록 + 비동기 색상/폰트 보강
│   │
│   ├── theme/
│   │   ├── app_theme.dart       # Material 3 테마
│   │   └── app_colors.dart      # 컬러 팔레트 + 마일스톤 테마 색상
│   │
│   ├── widgets/
│   │   ├── book_spine.dart      # 책등 위젯 (컬러 블록 + 세로 텍스트)
│   │   └── bookshelf_row.dart   # 서재 선반 한 줄 (드래그 정렬 지원)
│   │
│   └── utils/
│       ├── color_extractor.dart # palette_generator로 표지 색상 추출
│       ├── font_assigner.dart   # 장르 키워드 기반 책등 폰트 배정
│       └── constants.dart       # 상수값 (마일스톤 임계값 등)
│
├── features/
│   ├── auth/                    # 인증
│   │   ├── providers/auth_provider.dart
│   │   ├── screens/login_screen.dart
│   │   └── services/auth_service.dart     # 카카오 + Google OAuth
│   │
│   ├── bookshelf/               # 서재 (메인 화면)
│   │   ├── screens/
│   │   │   └── bookshelf_screen.dart      # 서재 메인 (커버/서가 뷰 토글)
│   │   ├── widgets/
│   │   │   ├── cover_feed_view.dart       # ✅ 커버 피드 뷰 (기본 뷰)
│   │   │   ├── featured_reading_card.dart # ✅ 읽는 중 피처드 카드
│   │   │   ├── feedback_cta_row.dart      # ✅ 피드백 유도 CTA
│   │   │   ├── book_cover_card.dart       # ✅ 표지 카드 (100×144)
│   │   │   └── cover_feed_section.dart    # ✅ 섹션 헤더 + 가로 스크롤 행
│   │   └── providers/
│   │       └── bookshelf_provider.dart    # 서재 상태 + 피드 providers
│   │
│   ├── search/                  # 책 검색 (무한 스크롤 페이지네이션)
│   │   ├── providers/book_search_provider.dart
│   │   ├── screens/book_search_screen.dart
│   │   └── widgets/book_search_result_card.dart
│   │
│   └── book_detail/             # 책 상세 + 피드백
│       ├── providers/book_detail_provider.dart  # rating/태그/리뷰 저장 (isSaving guard)
│       ├── screens/book_detail_screen.dart
│       └── widgets/
│           ├── rating_selector.dart        # 좋았다/보통/별로 토글
│           ├── emotion_tag_chips.dart       # 감성 태그 다중 선택
│           └── review_text_section.dart     # 자유 텍스트 + 가이드 질문
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

> **주의:** ERD 다이어그램은 주요 컬럼만 표시. books 테이블의 dominant_colors, mood_tags, spine_font 및 user_books의 shelf_order는 아래 테이블 상세 참조.

### 테이블 상세

#### `users`
Supabase Auth와 연동. 추가 프로필 정보 저장.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | Supabase Auth uid |
| email | text | 이메일 |
| nickname | text | 닉네임 |
| avatar_url | text | 프로필 이미지 |
| recommendation_confidence | jsonb | 추천 신뢰도 캐싱 (score, feedback_depth, genre_diversity 등) |
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
| source | text | 'aladin' / 'kakao' / 'data4library' |
| source_id | text | 외부 API의 고유 ID |
| sales_point | int | 알라딘 판매지수 (판매 기반, 신간 빠르게 반영) |
| loan_count | int | 정보나루 usageAnalysisList.book.loanCnt (누적 전체 대출수, 스테디셀러 지표) |
| loan_count_12mo | int | 정보나루 loanHistory 최근 12개월 합 (최근 독서 트렌드) |
| loan_count_source | text | 'usageAnalysisList' / 'loanItemSrch' / null (추적용) |
| loan_count_updated_at | timestamptz | loan_count 마지막 갱신 시점 |
| rich_description | text | YES24 상세 텍스트 (책소개/출판사리뷰/책속으로) |
| library_keywords | text[] | 정보나루 키워드 (예: {"인생","성장","자아찾기"}) |
| related_isbns | jsonb | 함께 빌린 책 ISBN (예: {"co_loan": ["978..."]}) |
| dominant_colors | jsonb | 표지 dominant color 2~3개 (hex 배열, 예: ["#3A2518","#8B6B4A","#D4C4A8"]) |
| mood_tags | text[] | LLM 자동 부여 무드 태그 (예: {"잔잔한","따뜻한"}) |
| spine_font | text | 책등 폰트 이름 (LLM 자동 배정, 예: 'Nanum Myeongjo') |
| created_at | timestamptz | 등록일 |
| updated_at | timestamptz | 갱신일 (auto trigger) |

#### `user_books`
유저의 서재. 책과 유저의 N:M 관계.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_id | uuid (FK → users) | |
| book_id | uuid (FK → books) | |
| status | text | 'read', 'reading', 'want_to_read' |
| shelf_order | int | 서가 뷰 드래그 정렬 순서 (유저가 직접 배치한 위치) |
| rating | text | 'good', 'neutral', 'bad' (호오 평가) |
| emotion_tags | jsonb | 감성 태그 다중 선택 (emotion_tag_options 참조) |
| review_text | text | 자유 텍스트 리뷰 |
| created_at | timestamptz | 등록일 |
| updated_at | timestamptz | 상태 변경일 |

> unique constraint: (user_id, book_id)

#### `feedbacks` (Phase 2~3 예정)
구조화된 카테고리별 피드백. MVP에서는 user_books의 rating/emotion_tags/review_text를 사용하고, Phase 2에서 상세 피드백 수집 시 활용 예정.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_book_id | uuid (FK → user_books) | |
| category | text | 'character', 'writing_style', 'worldbuilding', 'plot', 'message', 'atmosphere' |
| sentiment | text | 'positive' or 'negative' |
| free_text | text | 자유 텍스트 |
| created_at | timestamptz | |

#### `emotion_tag_options`
감성 태그 선택지. 앱에서 유저에게 보여줄 태그 목록.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| label | text | 태그 라벨 (예: '감동적인', '몰입감 있는') |
| sort_order | int | 정렬 순서 |
| is_active | boolean | 활성 여부 |
| created_at | timestamptz | |

#### `reflection_prompts`
리뷰 작성 도우미 질문. 카테고리별 가이드 질문.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| question | text | 질문 내용 |
| category | text (nullable) | 카테고리 (character, plot 등). NULL이면 범용 |
| is_active | boolean | 활성 여부 |
| created_at | timestamptz | |

#### `book_embeddings`
책의 벡터 표현. 2-Tier 임베딩 파이프라인.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| book_id | uuid (FK → books, unique) | |
| embedding | vector(1536) | OpenAI text-embedding-3-small 벡터 |
| tier | smallint (default 1) | 1=기본(메타데이터), 2=강화(YES24 rich_description) |
| source_text | text | 임베딩 생성에 사용된 원본 텍스트 |
| data_sources | jsonb (default []) | 사용된 데이터 소스 (예: ["aladin", "yes24_intro", "yes24_excerpt"]) |
| created_at | timestamptz | |
| updated_at | timestamptz | 갱신일 (auto trigger) |

> HNSW 인덱스 (`idx_book_embeddings_hnsw`) 적용 — 코사인 유사도 검색용

#### `batch_collection_state`
배치 수집 진행 상태 추적. 중단/재개 지원.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| source_type | text | 'item_list', 'author_search', 'keyword_search' |
| query_type | text | 'Bestseller', 'ItemNewAll' 등 |
| category_id | int | 카테고리 ID (item_list용) |
| search_keyword | text | 검색 키워드 (author/keyword_search용) |
| last_page_fetched | int | 마지막 처리 페이지 |
| total_items_found | int | 총 발견 아이템 수 |
| unique_items_saved | int | 신규 저장 수 |
| completed | boolean | 완료 여부 |
| updated_at | timestamptz | 갱신일 (auto trigger) |

> unique constraint: (source_type, query_type, category_id, search_keyword)

#### `user_taste_vectors` (Phase 1 MVP~)
유저의 취향 벡터. 가중 평균 단일 벡터 → K-means 클러스터별 다중 벡터로 자동 진화.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_id | uuid (FK → users) | |
| cluster_label | text | 취향 축 라벨 (NULL=단일 벡터, 'cluster_0'=K-means) |
| vector | vector(1536) | 취향 벡터 |
| weight | float | 클러스터 크기 가중치 (기본 1.0) |
| summary | text | LLM 취향 요약 (Phase 2) |
| method | text | 계산 방식 ('weighted_avg' 또는 'kmeans') |
| updated_at | timestamptz | |

#### `book_love_reasons` (Phase 2+ — 추천 엔진 v2)
책의 "좋아할 이유" 저장. 이유 기반 추천 매칭용.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| book_id | uuid (FK → books) | |
| reason | text | "호그와트의 수업, 기숙사, 퀴디치 등 디테일하게 구축된 마법 학교 생활" |
| reason_embedding | vector(2000) | OpenAI text-embedding-3-large 벡터 (Matryoshka 2000D) |
| source | text | 'llm_extracted' or 'user_feedback' |
| user_mention_count | int (default 0) | user_feedback 일 경우 언급 유저 수 |
| created_at | timestamptz | |

> ivfflat 인덱스 적용 (idx_blr_embedding) — 코사인 유사도 검색용

#### `user_taste_reasons` (Phase 2+ — 추천 엔진 v2)
유저의 "좋아하는 이유" 저장. 이유 기반 추천 매칭용.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| user_id | uuid (FK → users) | |
| book_id | uuid (FK → books) | 피드백이 입력된 책 |
| reason | text | "새롭고 디테일한 세계관" |
| reason_embedding | vector(2000) | OpenAI text-embedding-3-large 벡터 (Matryoshka 2000D) |
| weight | float (default 1.0) | rating 기반: good=1.0, neutral=0.5, bad=0.0 |
| created_at | timestamptz | |

> ivfflat 인덱스 적용 (idx_utr_embedding) — 코사인 유사도 검색용

#### `genre_embeddings` (Phase 3 — v3 추천 엔진)
고유 장르 텍스트의 임베딩. L1(중분류) ~24개 + L2(소분류) ~801개.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | uuid (PK) | |
| genre_text | text | "소설/시/희곡", "한국소설" 등 |
| level | text CHECK ('l1','l2') | 중분류 또는 소분류 |
| embedding | vector(2000) | OpenAI text-embedding-3-large 벡터 (Matryoshka 2000D) |
| created_at | timestamptz | |

> UNIQUE(genre_text, level)

#### `book_v3_vectors` (Phase 3 — v3 추천 엔진)
책별 desc 임베딩 + L1/L2 장르 FK. 대상: rich_description 보유 책.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| book_id | uuid (PK, FK → books) | |
| desc_embedding | vector(2000) | rich_description 기반 책 분위기 벡터 |
| source_text | text | 임베딩에 사용된 원본 텍스트 (디버깅용) |
| l1_text | text | 파싱된 L1 장르 텍스트 |
| l2_text | text | 파싱된 L2 장르 텍스트 |
| l1_genre_id | uuid (FK → genre_embeddings) | |
| l2_genre_id | uuid (FK → genre_embeddings) | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

> FK 인덱스: idx_book_v3_l1, idx_book_v3_l2

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
| **정보나루 API** (메인 배치) | 인기대출 도서 수집, usageAnalysisList 후처리로 loan_count/loan_count_12mo 통일 | 무제한 | 공공도서관 실제 대출 데이터. loan_count=누적, loan_count_12mo=최근 12개월 |
| **알라딘 API** (보완) | Bestseller/ItemNew 로 신간 커버 (정보나루 6~12개월 지연 보완), 메타데이터 보강 | 일 5,000회 | sales_point 기반. fallback_curation 에서 정보나루 top 20에 없는 신간 10권 보완 |

> - 유저의 실시간 검색은 카카오 API로 처리
> - 온보딩/fallback 책 풀은 **Strategy C (2026-04-16)**: 정보나루 `loan_count_12mo` top 20 (DISTINCT ON title) + 알라딘 `sales_point` top 10 = 30권. 상세 `docs/superpowers/specs/2026-04-16-data4library-aladin-hybrid-collection.md`
> - 큐레이션 내부 랭킹: 혼합 점수 `loan_count_12mo*2 + loan_count*1 + sales_point*0.5`
> - 검색 결과 중 유저가 선택한 책만 `books` 테이블에 저장

#### 서재 CRUD

| 동작 | Supabase 호출 | 설명 |
|------|--------------|------|
| 서재 조회 | `user_books.select('*, books(*)')` | 내 서재의 모든 책 + 책 정보 |
| 책 추가 | `books.upsert()` → `user_books.insert()` | 책 캐싱 후 서재에 추가 |
| 상태 변경 | `user_books.update({ status })` | 읽음/읽는중/읽고싶은 |
| 책 제거 | `user_books.delete()` | 서재에서 제거 |

#### 피드백 (MVP — user_books에 인라인 저장)

| 동작 | Supabase 호출 | 설명 |
|------|--------------|------|
| 평가 저장 | `user_books.update({ rating })` | 'good', 'neutral', 'bad' |
| 감성태그 저장 | `user_books.update({ emotion_tags })` | 다중 선택 태그 배열 |
| 리뷰 저장 | `user_books.update({ review_text })` | 자유 텍스트 |
| 태그 옵션 조회 | `emotion_tag_options.select()` | 앱에서 보여줄 태그 목록 |
| 가이드 질문 조회 | `reflection_prompts.select()` | 리뷰 도우미 질문 |

### 4-2. 추천 서버 API (Phase 3)

FastAPI 서버. Supabase DB에서 벡터 데이터를 읽어 추천 계산.

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `POST /embeddings/book/{book_id}` | POST | 책 임베딩 생성/갱신 |
| `POST /embeddings/user/{user_id}` | POST | 유저 취향 벡터 갱신 |
| `GET /recommendations/{user_id}` | GET | 추천 도서 리스트 |
| `GET /taste-profile/{user_id}` | GET | 취향 요약 (Phase 2) |

### 4-3. Supabase RPC 함수 목록

Supabase PostgreSQL에서 직접 실행 가능한 저수준 함수들.

| RPC 함수 | 입력 | 반환 | 설명 |
|---------|------|------|------|
| `recommend_books_for_user` | `user_id`, `limit` | `{book_id, title, score}` | v1 — recommend_books_by_reasons로 대체 예정 |
| `recommend_books_by_reasons` | `user_id`, `match_count` | `{book_id, title, score, matched_reason}` | v2 — 이유 임베딩 매칭 기반 추천 |
| `match_books_by_similarity` | `book_id`, `limit` | `{id, title, similarity_score}` | book-to-book 유사도 검색 (book_embeddings 기반) |
| `recompute_taste_vector_immediate` | `user_id` | `{success, updated_at}` | 유저 취향 벡터 즉시 재계산 |

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
  ├─ 1. 알라딘 배치 수집기 ✅ 구현 완료 + 자동화
  │   → 3-Layer 수집: Seed(ItemList) + Daily Batch(저자/키워드 검색) + Demand(앱 검색)
  │   → 라운드로빈 카테고리 순회 (장르 균형 확보)
  │   → yield rate 10% 미만 시 소스 자동 스킵
  │   → --daily-target으로 일일 수집량 제어
  │   → 30일 경과 소스 자동 리셋
  │   → GitHub Actions로 매일 KST 03:00 자동 실행
  │   → 스크립트: scripts/smart_batch_collector.py
  │
  ├─ 2. 책 메타데이터 강화기 ✅ 구현 완료 + 자동화
  │   → 색상 추출: colorthief로 표지 dominant color 추출
  │   → 폰트 배정: 장르 키워드 매칭으로 책등 폰트 자동 배정
  │   → YES24 스크래핑: 책소개 + 출판사리뷰 + 책속으로 → rich_description
  │   → 색상/폰트: daily-pipeline collect job (KST 03:00) 내 자동 실행
  │   → YES24: daily-scrape (6시간마다 240권) 자동 실행
  │   → 스크립트: scripts/batch_enricher.py, scripts/yes24_scraper.py
  │
  ├─ 3. 책 임베딩 생성기 ✅ 구현 완료 + 자동화
  │   → Tier 1: title + author + genre + description → 기본 임베딩
  │   → Tier 2: rich_description(YES24) 기반 강화 임베딩
  │   → OpenAI text-embedding-3-small (1536차원)
  │   → Tier 1: daily-pipeline collect job 내 자동 실행 (KST 03:00)
  │   → Tier 2: daily-pipeline enrich job 내 자동 실행 (KST 03:00)
  │   → 스크립트: scripts/tier1_embedder.py, scripts/tier2_embedder.py
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

### 자동화 현황

```
[자동 — GitHub Actions, 매일 KST 03:00 (daily-pipeline.yml)]
  [병렬] discovery job:
    1. data4library_discovery_collector.py --tier 1    (정보나루 대출 순위 수집)
    2. backfill_genre.py --limit 300                   (genre NULL 책 알라딘 보강)
  [병렬] collect job:
    1. smart_batch_collector.py --daily-target 200     (알라딘 수집)
    2. batch_enricher.py --limit 500                   (색상 추출 + 폰트 배정)
    3. tier1_embedder.py                               (Tier 1 임베딩 생성)
  [병렬] enrich job:
    1. generate_book_v3_vectors.py                     (v3 desc + l1/l2 장르 벡터)
    2. v3_reason_extract.py --limit 200                (LLM reason 추출 + 임베딩)
    3. tier2_embedder.py                               (Tier 2 임베딩 생성)
  [순차] build-and-recompute job (위 3개 완료 후):
    1. build_index.py --incremental                    (추천 인덱스 빌드)
    2. taste_recomputer.py --limit 500                 (취향 벡터 재계산)

[자동 — GitHub Actions, 6시간마다 (daily-scrape.yml)]
  1. yes24_scraper.py --limit 240                      (YES24 rich_description 수집)

[수동 — workflow_dispatch]
  - build-index.yml                                    (인덱스 수동 빌드)
  - daily-collect.yml, daily-embed-t2.yml 등           (개별 step 수동 실행)
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
[책 임베딩 — 2-Tier 파이프라인]
Tier 1 (기본, daily-pipeline collect job KST 03:00):
→ books.title + author + genre + description
→ OpenAI text-embedding-3-small API 호출
→ book_embeddings (tier=1)

Tier 2 (강화, daily-pipeline enrich job KST 03:00):
→ books.rich_description (YES24 책소개/출판사리뷰/책속으로)
→ title + author + genre + description + 책소개 + 발췌 조합
→ OpenAI text-embedding-3-small API 호출
→ book_embeddings (tier=2) — Tier 1을 덮어씀

[책 벡터 강화]
유저 피드백 n개 이상 쌓인 책
→ 기존 description + 유저들의 피드백 텍스트 종합
→ 재임베딩 → 벡터 업데이트
→ 데이터 많을수록 벡터 품질 향상

[유저 취향 벡터]
피드백 n개 이상 쌓이면 트리거
→ user_books에서 유저의 rating, emotion_tags, review_text 조회
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

## 5-2. 임베딩 모델 설정 (Dual Model)

이유 기반 추천 도입 (Phase 2+) 시 **두 개의 임베딩 모델**을 병렬로 운영.

| 모델 | 차원 | 용도 | 비용 | 주의사항 |
|------|------|------|------|---------|
| **text-embedding-3-small** | 1536 | book_embeddings (book-to-book 유사도) | 저비용 | 기존 운영 유지 |
| **text-embedding-3-large** | 2000 (Matryoshka) | book_love_reasons, user_taste_reasons, genre_embeddings, book_v3_vectors (v3 추천) | 중비용 | 정확도 높음, pgvector 최대 2000D |

**중요 주의사항:**
- 두 모델의 벡터는 **서로 비교 불가능** (차원이 다름)
- book-to-book 유사도 검색: book_embeddings(small) 만 사용
- 이유 기반 추천: book_love_reasons + user_taste_reasons (large) 만 사용
- 혼용 시 오류 발생 → 데이터 스키마 설계 단계에서 신경 쓰기

**v2 추천 정확도:**
- text-embedding-3-small 대비 정확도: 83% → 100% (테스트 케이스)
- 예: "재밌는 소재" ↔ "과거와 현재를 오가는 편지 매개 시간여행 소재" → small에서 미매칭, large에서 1위

---

## 6. 서재 UI 기술 레퍼런스

### Flutter 패키지

| 패키지 | 용도 | 링크 |
|--------|------|------|
| `palette_generator` | 표지 이미지에서 dominant color 추출 → 책등 색상 자동 생성 | [pub.dev](https://pub.dev/packages/palette_generator_master) |
| `flutter_animate` | 책 꽂히는 모션, 마일스톤 celebration 등 커스텀 애니메이션 | [pub.dev](https://pub.dev/packages/flutter_animate) |
| `forui` | 40+ 미니멀 위젯 라이브러리 (버튼, 카드, 인풋 등 기본 UI) | [forui.dev](https://forui.dev/docs) / [GitHub](https://github.com/duobaseio/forui) |

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
| **서재** | 커버 피드 + 서가 뷰 하이브리드, 드래그 정렬, 마일스톤 배경 | - |
| **책등 생성** | 표지 색상 추출 + 무드 기반 폰트 배정 (LLM) | - |
| **무드 태그** | 책 메타데이터 → LLM 무드 태그 자동 부여 | 피드백 기반 정확도 개선 |
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
