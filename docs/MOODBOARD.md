# Moodboard — 서재 UI & 온보딩 레퍼런스

---

## 1. 서재 UI 방향성

### 확정 방향 (2026-03-22)

**하이브리드 뷰: 커버 피드(기본) + 서가 뷰(전환)**

| 뷰 | 역할 | 스타일 |
|---|---|---|
| 커버 피드 | 자동 큐레이션, 탐색 | 넷플릭스형 가로 스크롤 행, 실제 표지 이미지 |
| 서가 뷰 | 수동 정리, 감상 | 컬러 블록 책등, 드래그 정렬, 플랫 디자인 |

### 키워드
`clean white surface` · `color block spines` · `cover-art-centric feed` · `Netflix-style rows` · `mood-based curation` · `drag-to-arrange`

### 비주얼 원칙

| 원칙 | 설명 |
|------|------|
| **깨끗한 톤** | 순백 배경 (#FFFFFF). 따뜻함은 타이포와 여백으로 표현 |
| **커버가 주인공 (피드)** | 커버 피드에서는 실제 표지 이미지가 핵심 비주얼 |
| **컬러 블록 책등 (서가)** | 표지 dominant color 2~3개를 영역 분할로 배치 (그라데이션 X). 책마다 고유 폰트 |
| **성장이 보이는 구조** | 마일스톤마다 배경/분위기 변화 (서재 크기가 아닌 테마 전환) |
| **내가 꾸미는 서재** | 서가 뷰에서 롱프레스 → 드래그로 직접 정리 |

### 디자인 레퍼런스 — 서재

#### 앱 레퍼런스 (2026-03-22 리서치)

| 앱 | 참고 포인트 |
|---|---|
| **Netflix** | 카테고리별 가로 스크롤 행 구조. 피드가 곧 개인화 |
| **Letterboxd** | 다크 테마에서 커버가 돋보이는 레이아웃. 포스터 3열 그리드, 콘텐츠 중심 미니멀 |
| **Literal Club** | 깔끔한 미니멀 디자인, 여백 활용. "quick, clean, beautiful" |
| **StoryGraph** | 무드/페이싱 기반 시각화 (우리 무드 태그 참고). 모노크롬 팔레트 |
| **Bookbear** | 13가지 테마 커스텀, 큰 커버 이미지. 캘린더 뷰 |
| **Bookshelf (App)** | "Liquid Glass" (iOS 26), 깔끔한 카드 UI. 다크모드 |
| **Spotify** | 앨범 dominant color 추출 → 배경 적용. 60-30-10 컬러 법칙 |

#### 디자인 리소스

| 출처 | 링크 | 참고 포인트 |
|------|------|-------------|
| Dribbble | [Bookshelf App 태그](https://dribbble.com/tags/bookshelf-app) | 전반적인 서재 앱 UI 트렌드 |
| Dribbble | [Book Spines 태그](https://dribbble.com/tags/book_spines) | 책등 디자인 |
| Behance | [Shelves Book Library App](https://www.behance.net/gallery/107971283/Shelves-Book-Library-App-UIUX-Design) | 서재 앱 케이스 스터디 |
| Medium | [Library App UX Case Study](https://askarra.medium.com/library-app-ux-case-study-a7906ccada7f) | Brown 프라이머리 + DM Serif/Sans 조합 |
| Pinterest | [Book Spine Design Ideas](https://www.pinterest.com/ideas/book-spine-design-ideas/936388988976/) | 책등 디자인 영감 |
| CSS | [38 CSS Book Effects](https://freefrontend.com/css-book-effects/) | CSS 3D 책 렌더링 기법 |

### 책등 디자인 — 컬러 블록 방식 (확정)

```
┌──────────────────────────────────────────────┐
│                                              │
│  ┌────┐┌──┐┌────┐┌─┐┌───┐┌──┐┌─────┐        │
│  │    ││  ││    ││ ││   ││  ││     │        │
│  │작별│ │채││소년││흰││아몬││불││파친 │        │
│  │하지│ │식││이  ││ ││드  ││편││코   │        │
│  │않는│ │주││온다││ ││   ││한││     │        │
│  │다  │ │의││    ││ ││   ││편││     │        │
│  │    │ │자││    ││ ││   ││의││     │        │
│  │한강│ │  ││한강││ ││손원││점││이민 │        │
│  │    │ │한││    ││한││평  ││김││진   │        │
│  │ ▬▬ │ │강││ ▬▬ ││강││   ││호││     │        │
│  │    │ │  ││    ││ ││   ││연││ ▬▬  │        │
│  └────┘└──┘└────┘└─┘└───┘└──┘└─────┘        │
│  ──────────────────────────────────────────  │
│                                              │
│         · 길게 눌러서 위치를 바꿔보세요 ·        │
│                                              │
└──────────────────────────────────────────────┘

특징:
- 각 책등 = 표지 dominant color 2~3개를 컬러 블록으로 분할 (60/30/10)
- 그라데이션 아님. 솔리드 컬러 영역 구분
- 제목 = 주색 블록에 배치, 저자 = 보조색 블록에 배치
- 폰트 = 장르/무드에 따라 5~6개 한국어 폰트 중 자동 배정
- 선반 = 심플 라인 (나무 질감 없음)
- 롱프레스 → 드래그로 위치 변경 가능
```

### 마일스톤 — 배경/분위기 변화 (서재 크기 아님)

| 권수 | 테마 | 분위기 |
|------|------|--------|
| 0~9권 | 순백 #FFFFFF (기본 배경) | 깨끗한 시작 |
| 10~29권 | 우드톤 + 은은한 조명 | 나만의 공간 |
| 30~49권 | 짙은 라이브러리 톤 + 벽면 텍스처 | 본격 독서가 |
| 50~99권 | 다크 우드 + 골드 액센트 | 자부심 |
| 100권+ | 풀 다크 라이브러리 + 특별 이펙트 | 궁극의 서재 |

---

## 2. 온보딩 레퍼런스

### 핵심 원칙

> **3분 안에 끝나야 한다. 선택하는 과정 자체가 재밌어야 한다. 끝나면 즉시 성취감.**

### 레퍼런스 앱 분석

#### Spotify — 선택의 쾌감
- 링크: [Spotify Onboarding Deep-dive (Medium)](https://medium.com/@smarthvasdev/deep-dive-into-spotifys-user-onboarding-experience-f2eefb8619d6)
- 링크: [Spotify iOS Onboarding Flow (Mobbin)](https://mobbin.com/explore/flows/2ca9968b-a50d-4910-89e7-e894023d7d21)
- 링크: [Spotify Onboarding Case Study (Behance)](https://www.behance.net/gallery/165941519/Spotify-Music-Curation-Onboarding-(UX-Case-Study))
- **참고 포인트**: 아티스트 선택 시 버블이 커지는 애니메이션. 고를수록 화면이 풍성해지는 시각적 보상. 선택이 곧 개인화의 시작이라는 인식.

#### Letterboxd — 포스터 기반 시각적 온보딩
- 링크: [Letterboxd Onboarding 개선안 (Behance)](https://www.behance.net/gallery/223558121/Improving-New-User-Flow-Onboarding-in-Letterboxd)
- 링크: [Letterboxd UX Case Study](http://www.roberthanlydesign.com/ux-letterboxd)
- 링크: [Letterboxd JTBD Case Study (growth.design)](https://growth.design/case-studies/letterboxd-jobs-to-be-done)
- **참고 포인트**: "Track, Save, Tell" 3단계 가치 제안. 장르/언어/스트리밍 서비스 선택으로 개인화. 포스터 비주얼이 온보딩을 풍성하게 만듦.

#### Blinkist — 목적 기반 온보딩
- **참고 포인트**: "왜 읽으려고 해?" 목적 질문으로 시작. 장르 선택 → 즉시 책 추천 보여줌. 의도(intent) 수집이 자연스러움.

#### 일반 모바일 온보딩 모범사례
- 링크: [Mobile UX Design Examples (Eleken)](https://www.eleken.co/blog-posts/mobile-ux-design-examples)
- 링크: [Best Mobile App Onboarding Examples 2026 (Plotline)](https://www.plotline.so/blog/mobile-app-onboarding-examples)
- 링크: [UX Onboarding Best Practices 2025](https://www.uxdesigninstitute.com/blog/ux-onboarding-best-practices-guide/)
- 링크: [In-App Onboarding Guide (Appcues)](https://www.appcues.com/blog/in-app-onboarding)

### 우리 앱에 적용

```
[온보딩 플로우]

1. 웰컴 스크린
   "당신만의 서재를 만들어볼까요?"

2. 빠른 책 선택 (핵심!)
   - 베스트셀러/유명 책 표지 그리드
   - 탭하면 서재 미니맵에 책등 꽂히는 애니메이션
   - 장르 탭으로 분류 (소설 / 에세이 / 자기계발 / SF / ...)
   - 검색으로 직접 추가도 가능
   - 최소 5권 선택 유도 (진행바 표시)

3. 첫 피드백
   - "제일 좋았던 책 하나만 골라볼까?"
   - 간단한 가이드 질문 1~2개
   - "이 책에서 뭐가 좋았어?" 선택지 + 한 줄 자유 텍스트

4. 서재 완성
   - "당신의 서재가 시작됐어요!"
   - 채워진 서재 전체 화면 (뿌듯함)
   - "더 채워볼까요?" CTA
```

---

## 3. 경쟁 앱 참고

| 앱 | 장점 | 우리가 가져올 것 | 우리가 다르게 할 것 |
|------|------|-----------------|-------------------|
| **북적북적** | 한국 도서 DB, 독서 기록 | 한국 유저 니즈 이해 | 서재 감성 + 추천 엔진 |
| **Goodreads** | 거대한 커뮤니티, DB | 리뷰/별점 구조 참고 | 취향 분석에 집중 (소셜 < 개인화) |
| **StoryGraph** | 무드 기반 추적, 통계 | 무드 시각화, 피드백 클러스터 | 서재 경험 + 자연어 피드백 |
| **Letterboxd** | 감성적 로깅 경험, 포스터 중심 | 콘텐츠 중심 미니멀 UI | 영화→도서 도메인 전환 |
| **Literal Club** | 깔끔한 미니멀 디자인 | 여백 활용, 커버 그리드 | 커버 피드 + 서가 뷰 하이브리드 |
| **Bookbear** | 테마 커스터마이징, 큰 커버 | 마일스톤 테마 변화 참고 | 무드 태그 기반 자동 큐레이션 |

- 경쟁 앱 리스트 참고: [Best Book Tracking Apps 2026 (Headway)](https://makeheadway.com/blog/best-book-tracking-app/)
- 도서 앱 개발 가이드: [How to Create Book Tracking Apps (TekRevol)](https://www.tekrevol.com/blogs/book-tracking-app/)

---

## 4. 추가 탐색 과제

- [x] ~~서재 UI 방향 확정~~ → 커버 피드 + 서가 뷰 하이브리드 (2026-03-22)
- [x] ~~책등 디자인 방식 확정~~ → 컬러 블록 + 무드 기반 폰트 배정 (2026-03-22)
- [ ] 온보딩 플로우 와이어프레임 (Figma 또는 HTML 프로토타입)
- [ ] 마일스톤 배경/분위기 변화 시안 (5단계 테마 컬러 구체화)
- [ ] 컬러 블록 배치 패턴 다양화 (60/30/10 외 다른 비율/배치 실험)
- [ ] 커버 피드 비어있을 때 (책 0~2권) 빈 상태 디자인
