# 책 등록 파이프라인 설계

> 검색 → 서재 추가의 전체 워크플로우를 하나의 파이프라인으로 구현.
> 색상 추출, 폰트 배정, 서재 저장을 포함.

---

## 1. 개요

사용자가 책을 검색해서 서재에 추가할 때 필요한 모든 처리를 하나의 파이프라인으로 통합한다.

**범위:**
- 검색 결과에서 서재 추가 (상태 선택 포함)
- 표지 이미지 dominant color 추출
- 장르 기반 책등 폰트 자동 배정
- DB 스키마 마이그레이션 (누락 컬럼 추가 + RLS 정책)
- 패키지 추가 (`palette_generator`, `google_fonts`)

**범위 밖 (후속 작업 필수):**
- **상태 변경** ("읽는 중" ↔ "다 읽음") — 현재 상태 변경 경로가 없음. 책 상세 화면 설계 시 반드시 반영 필요. 이 기능 없이는 서재 워크플로우가 불완전함.
- 드래그 앤 드롭 정렬 (shelf_order 컬럼은 이번에 추가하되, UI는 별도)
- 피드백 수집 UI
- 온보딩 플로우

---

## 2. 사용자 플로우

```
서재 화면 → 검색 버튼/FAB → 검색 화면
  → 책 제목/저자 입력 → 검색 결과 리스트
    → 이미 서재에 있는 책: "추가됨" 뱃지, 탭 비활성
    → 새 책 탭 → 상태 선택 바텀시트 ("읽는 중" / "다 읽었어요")
      → 선택 즉시 등록 시작 (탭 비활성화로 중복 방지)
      → 스낵바 "서재에 추가됨"
      → 검색 화면에 머물기 (연속 추가 가능)
      → 해당 검색 결과에 "추가됨" 뱃지 전환
```

---

## 3. 등록 파이프라인 상세

### 3-1. 즉시 처리 (동기)

1. **books upsert** — ISBN 기준 중복 방지
   - 카카오 API 응답에서 Book 객체 생성
   - `toJson()`에서 `id` 필드 제외하여 upsert (DB가 UUID 자동 생성)
   - ISBN이 null인 책은 매번 새 행으로 insert (PostgreSQL은 NULL을 unique로 취급하지 않음)
   - Supabase `books` 테이블에 upsert (conflict on isbn)
   - 반환: book_id

2. **user_books insert** — 유저-책 관계 생성
   - `user_id` + `book_id` + `status` (reading/read)
   - unique(user_id, book_id) 제약 → 이미 있으면 "이미 서재에 있어요" 안내
   - 성공 시 `bookshelfProvider` 리프레시

> **참고:** DB 스키마에 `want_to_read` 상태가 정의되어 있으나, MVP에서는 의도적으로 `reading`/`read`만 사용. `want_to_read`는 후속 버전에서 추가 예정.

### 3-2. 백그라운드 처리 (비동기)

등록 완료 후 백그라운드에서 실행. 실패해도 사용자 흐름 차단하지 않음.

3. **dominant color 추출**
   - `palette_generator` 패키지로 표지 이미지(cover_url) 분석
   - `PaletteGenerator.fromImageProvider(NetworkImage(url), maximumColorCount: 3)`
   - dominant 2~3색을 hex 배열로 변환
   - `books.dominant_colors` 컬럼 업데이트 (jsonb 타입)
   - 실패 시: 해시 기반 폴백 (BookSpine 기존 로직) — 로그만 남김

4. **spine font 배정**
   - `FontAssigner.assignFont(genre: book.genre, description: book.description)`
   - 장르 키워드 매칭으로 7종 중 선택 (폴백: Pretendard)
   - `books.spine_font` 컬럼 업데이트

> **`page_count` 참고:** 카카오 책 검색 API는 page_count를 제공하지 않음. 현재 BookSpine은 null일 때 기본값 250을 사용하므로, 별도 소싱 없이 기본값 유지. 추후 알라딘 배치 수집에서 보강 가능.

---

## 4. DB 스키마 마이그레이션

### 004_book_enrichment.sql

```sql
-- 책 메타데이터 확장 (색상, 폰트, 무드태그)
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS dominant_colors jsonb DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS spine_font text DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS mood_tags jsonb DEFAULT NULL;

-- 서가 정렬 순서
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS shelf_order int DEFAULT NULL;

-- books UPDATE 정책 (인증된 유저가 메타데이터 업데이트 가능)
CREATE POLICY "인증된 유저가 책 메타데이터 업데이트"
  ON public.books FOR UPDATE
  USING (true)
  WITH CHECK (auth.role() = 'authenticated');
```

> **`mood_tags` 참고:** 이번 파이프라인에서 mood_tags를 채우지 않음. 컬럼만 미리 추가하고, 배치 수집 파이프라인(scripts/)에서 LLM 기반으로 별도 채울 예정.

---

## 5. 패키지 추가

| 패키지 | 용도 |
|--------|------|
| `palette_generator` | 표지 이미지 dominant color 추출 |
| `google_fonts` | 책등 폰트 7종 런타임 다운로드 + 캐싱 |

---

## 6. 코드 변경 상세

### 6-1. 신규 파일

**`app/lib/core/services/book_registration_service.dart`**
- `registerBook(Book book, BookStatus status)` — 파이프라인 오케스트레이션
  - 동기: books upsert (id 필드 제외) → user_books insert
  - 비동기: color extraction → font assignment → DB update
- `isBookInShelf(String isbn)` — 서재 중복 체크

> **참고:** Phase 1에서는 `FontAssigner`(키워드 매칭)가 폰트를 배정. ARCHITECTURE.md의 `mood_tag_service.dart`(LLM 기반)는 Phase 2에서 구현하여 FontAssigner를 대체할 예정.

### 6-2. 수정 파일

**`app/lib/core/utils/color_extractor.dart`**
- `extractFromUrl()` 스텁 → `palette_generator` 실제 구현

**`app/lib/core/models/book.dart`**
- `toJson()` — upsert용 메서드 추가 (id 필드 제외)

**`app/lib/core/widgets/book_spine.dart`**
- `_fontFamily` → `google_fonts` 연동
- `GoogleFonts.getFont(_fontFamily)` → TextStyle 전체를 반환하므로, fontFamily 문자열 대신 TextStyle 직접 사용

**`app/lib/features/search/screens/book_search_screen.dart`**
- 검색 결과 탭 → 상태 선택 바텀시트 호출
- 등록 중 탭 비활성화 (중복 호출 방지)
- "추가됨" 뱃지 표시 로직

**`app/lib/features/search/widgets/book_search_result_card.dart`**
- `isAdded` 파라미터 추가 → "추가됨" 뱃지 UI

**`app/lib/features/search/providers/book_search_provider.dart`**
- 유저 서재 ISBN 목록 보유 → 검색 결과와 교차 확인

**`app/lib/features/bookshelf/providers/bookshelf_provider.dart`**
- `addBook()` mutation 메서드 추가 (BookRegistrationService 호출)

**`app/pubspec.yaml`**
- `palette_generator`, `google_fonts` 의존성 추가

### 6-3. 신규 마이그레이션

**`supabase/004_book_enrichment.sql`**

---

## 7. 에러 처리

| 상황 | 처리 |
|------|------|
| books upsert 실패 | 스낵바 에러, 등록 중단 |
| user_books insert 실패 (중복) | "이미 서재에 있어요" 스낵바 |
| user_books insert 실패 (기타) | 스낵바 에러, 등록 중단 |
| 색상 추출 실패 (네트워크/파싱) | 해시 기반 폴백, 로그 기록 |
| 폰트 배정 실패 | Pretendard 폴백 (기존 로직) |
| ISBN 없는 책 upsert | 매번 새 행으로 insert (NULL unique 미적용) |
| 연속 빠른 탭 | 탭 비활성화로 중복 등록 방지 |

---

## 8. 테스트 계획

- **유닛 테스트**: BookRegistrationService — upsert (id 제외 확인), 중복 체크, 에러 처리
- **유닛 테스트**: ColorExtractor — 실제 이미지 색상 추출, hex 변환, 실패 폴백
- **유닛 테스트**: FontAssigner — 키워드 매칭 정확성
- **위젯 테스트**: 상태 선택 바텀시트 — 선택 후 콜백, 바텀시트 dismiss
- **위젯 테스트**: BookSearchResultCard — "추가됨" 뱃지 표시/비활성
- **통합 테스트**: 검색 → 선택 → 등록 → 서재 반영 전체 흐름
