# Curation Project

컨텐츠 소비 피드백 기반 취향 분석 & 추천 앱. MVP는 도서 카테고리로 시작.

## 프로젝트 문서

모든 기획/설계 문서는 `docs/`에 있다. 코드 작성 전에 반드시 참고할 것.

| 문서 | 내용 |
|------|------|
| `docs/PRODUCT_PLAN.md` | 프로덕트 비전, 로드맵, 유저 플로우 |
| `docs/ARCHITECTURE.md` | 시스템 구조, DB 스키마, API 설계, 내부 엔진 |
| `docs/MOODBOARD.md` | 서재 UI 레퍼런스, 온보딩 레퍼런스 |
| `docs/decisions/` | 의사결정 로그 (ADR) |
| `docs/meeting-notes/` | 논의 기록 |

## 기술 스택

- **앱**: Flutter + Forui (UI 라이브러리)
- **상태 관리**: Riverpod (개발자 리뷰 후 변경 가능)
- **백엔드**: Supabase (Auth, PostgreSQL + pgvector, Storage)
- **책 검색 API**: 카카오 (메인) + 알라딘 (배치 수집)
- **인증**: 카카오 + Google + Apple 소셜 로그인
- **임베딩**: OpenAI text-embedding-3-small
- **추천 서버**: Python FastAPI (Phase 3)

## 개발 규칙

### 브랜치 전략
- `main` — 안정 브랜치
- `feature/*` — 기능 개발
- `fix/*` — 버그 수정

### 커밋 메시지
```
<type>: <한국어 설명>

<상세 내용 (선택)>
```
type: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### 코드 스타일
- Flutter/Dart 공식 스타일 가이드 준수
- feature-first 디렉토리 구조 (`lib/features/`, `lib/core/`)
- 공통 모듈은 `lib/core/`에서 관리

### 테스트
- 새 기능은 테스트와 함께 작성
- 위젯 테스트 + 유닛 테스트 최소 커버리지 유지

### API 키 관리
- `.env` 파일에 보관, 절대 커밋하지 않음
- `.gitignore`에 `.env` 포함 필수

## 팀

| 역할 | 담당 |
|------|------|
| PM | Eden |
| 풀스택 개발 | (개발자) |

## Superpowers 플러그인

이 프로젝트는 [Superpowers](https://github.com/obra/superpowers) 워크플로우를 사용한다.

설치:
```bash
/plugin install superpowers@claude-plugins-official
```
