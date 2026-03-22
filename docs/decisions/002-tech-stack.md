# ADR-002: 기술 스택 제안

- **날짜**: 2026-03-18
- **상태**: 부분 확정 (책 DB 변경됨: 알라딘+Google Books → 카카오(메인)+알라딘(보완). 나머지는 개발자 리뷰 필요)
- **참여자**: Eden (PM)

---

## 배경

2인 팀(PM + 풀스택 개발자)으로 iOS + Android 동시 출시가 필요한 앱을 만든다.

## 제안 스택

| 영역 | 스택 | 선택 이유 |
|------|------|-----------|
| 앱 | Flutter | 크로스플랫폼, 서재 애니메이션 구현에 강함, 코드베이스 하나 |
| 백엔드 | Supabase | 인증/DB/스토리지 올인원, 서버 관리 부담 없음 |
| 책 DB | ~~알라딘 API + Google Books API~~ → **카카오 (메인) + 알라딘 (보완)** | 카카오: 실시간 검색, 알라딘: 베스트셀러 배치. Google Books 제외 |
| 임베딩 | OpenAI text-embedding-3-small | 비용 저렴, 품질 충분 |
| 벡터 저장 | Supabase pgvector | 별도 벡터DB 없이 해결 |
| 추천 로직 | Python (FastAPI) | 벡터 계산 생태계 최적 |

## Flutter를 선택한 이유

- React Native 대비: 서재 애니메이션(책 꽂히는 모션, 스크롤 등)에서 자유도 높음
- 네이티브 각각 개발: 2인 팀에서 비현실적
- Dart 러닝커브 낮음

## Supabase를 선택한 이유

- Firebase 대비: PostgreSQL 기반으로 pgvector 활용 가능 (벡터 저장 + 일반 DB가 하나)
- 자체 서버 구축 대비: 초기 인프라 관리 부담 제거

## 미결사항

- 개발자가 Flutter/Supabase 경험 있는지 확인 필요
- 알라딘 API 사용 조건 확인 (rate limit, 비용 등)
- 추천 로직 서버를 별도로 둘지, Supabase Edge Function으로 처리할지
