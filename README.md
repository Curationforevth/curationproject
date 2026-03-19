# Curation Project

컨텐츠 소비 피드백 기반 취향 분석 & 추천 앱.
MVP는 도서 카테고리로 시작, 장기적으로 영화/뮤지컬/전시 등으로 확장 예정.

## 핵심 컨셉

1. **감성 서재** — 읽은 책을 꽂아가는 뿌듯함
2. **취향 발견** — 피드백 기반으로 "나는 이런 독자구나"
3. **맞춤 추천** — 벡터 유사도 기반 도서 추천

## 기술 스택

| 영역 | 스택 |
|------|------|
| 앱 | Flutter + Forui |
| 백엔드 | Supabase (PostgreSQL + pgvector) |
| 책 검색 | 카카오 API (메인) + 알라딘 API (배치) |
| 인증 | 카카오 + Google + Apple |
| 임베딩 | OpenAI text-embedding-3-small |
| 추천 서버 | Python FastAPI (Phase 3) |

## 문서

| 문서 | 내용 |
|------|------|
| [PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md) | 프로덕트 비전, 로드맵, 유저 플로우 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 시스템 구조, DB 스키마, API 설계 |
| [MOODBOARD.md](docs/MOODBOARD.md) | 서재 UI & 온보딩 레퍼런스 |
| [decisions/](docs/decisions/) | 의사결정 로그 (ADR) |

## 개발 환경 설정

```bash
git clone https://github.com/Curationforevth/curationproject.git
cd curationproject
```

Claude Code 사용 시 `CLAUDE.md`가 자동으로 로드됩니다.

Superpowers 플러그인 설치:
```bash
/plugin install superpowers@claude-plugins-official
```

## 팀

- **PM**: Eden
- **풀스택 개발**: TBD
