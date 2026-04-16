# 도서 수집 & 임베딩 파이프라인 설계

> 작성일: 2026-03-20
> **업데이트 2026-04-16**: loan_count 소스 통일 + 알라딘 혼합 전략 도입. 상세는 `2026-04-16-data4library-aladin-hybrid-collection.md` 참조. 아래 본문 중 "SalesPoint 기준" 관련 내용은 섹션 8의 업데이트로 대체됨.
> 상태: 설계 확정, 구현 대기
> 리뷰: 스펙 리뷰 통과 (Critical/Important 이슈 반영 완료)

---

## 1. 목적

이 서비스는 취향 벡터 기반 도서 추천 앱이다. 추천 정확도는 벡터 공간의 밀도와 품질에 직결된다. 따라서 도서 DB 수집 전략의 목표는:

1. **유저가 찾을 확률이 높은 책**을 우선 확보
2. garbage data(아무도 안 읽는 책) 최소화
3. 벡터 공간을 풍부하고 정밀하게 구축
4. API 콜 낭비 없이 효율적으로 수집
5. 사람 손 안 타고 자동으로 동작
6. 나중에 갈아엎지 않을 확장 가능한 구조

---

## 2. 전체 구조: 3-Layer 아키텍처

```
┌─────────────────────────────────────────────┐
│            Seed Layer (출시 전 1회)            │
│  Phase 1 스윕 → ~10,000권 인기 도서 확보       │
│  + Tier 1 즉시 임베딩                         │
└─────────────────────────────────────────────┘
                    ↓ 출시
┌─────────────────────────────────────────────┐
│         Demand Layer (앱 검색 시 상시)         │
│  DB 히트 → 즉시 반환                          │
│  DB 미스 → 카카오 API → 캐싱 + 비동기 임베딩    │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│       Daily Batch (매일 자동, 새 책 1,000건)   │
│  3-Phase 수집 (인기순) + Tier 1 즉시 임베딩    │
│  yield rate 기반 스마트 스킵                   │
└─────────────────────────────────────────────┘
```

### 2-1. Seed Layer

출시 전 1회 실행. 검증된 인기 도서로 초기 DB를 채움.

- 기존 `smart_batch_collector.py` Phase 1 그대로 활용
- 17개 카테고리 × 5 QueryType × 4페이지 = ~340 API콜
- SalesPoint(실제 판매량) 기준 인기순 → 유저 수요와 가장 가까운 프록시
- 예상 ~8,000~10,000권, 하루 만에 완료
- 수집 직후 Tier 1 임베딩 일괄 생성

### 2-2. Demand Layer

앱 서비스 중 상시 동작. 유저 검색 시 DB 우선, 부족하면 API 보충.

```
유저 검색 입력
  ↓
Supabase books 테이블 조회
  ├─ 결과 충분 → 즉시 반환 (API 콜 0)
  └─ 결과 부족 → 카카오 API 호출 → 결과 반환
                    ↓
              유저가 선택한 책 → books에 upsert (캐싱)
                    ↓
              비동기 임베딩 큐 → Tier 1 임베딩 생성
```

- DB 결과가 **5건 미만**이면 카카오 API로 보충
  - 5건 기준: 검색 결과로 최소한의 선택지를 제공하는 임계치
  - 향후 유저 행동 데이터로 조정 가능
- DB가 커질수록 카카오 API 의존도 자연 감소
- 캐싱된 책의 Tier 1 임베딩: **Supabase Edge Function** (service role)으로 비동기 생성
  - 앱 클라이언트(유저 컨텍스트)에서 직접 book_embeddings에 쓰지 않음
  - books에 INSERT 이벤트 → Edge Function 트리거 → OpenAI API → 임베딩 저장
  - service role 사용으로 RLS 우회

### 2-3. Daily Batch

매일 자동 실행. DB에 없는 새 책 1,000건을 채움.

- 기존 3-Phase 전략 유지:
  - Phase 1 (카테고리 인기 리스트): 중복 최소, 먼저 실행
  - Phase 2 (저자 검색): DB 저자 + 큐레이션 저자, SalesPoint순
  - Phase 3 (키워드 검색): 문학상/시리즈/장르/트렌드
- **"새 책 1,000건"** = DB에 새로 저장된 건수 기준 (중복 스킵분 미포함)
- 수집 직후 Tier 1 임베딩 자동 생성
- **1,000건 목표의 현실성**: DB가 커질수록 yield rate 하락 → API 콜 증가
  - DB 10K: ~20~30% yield → ~70~150 API콜로 1,000건 (여유)
  - DB 50K: ~10~15% yield → ~150~300 API콜 (여유)
  - DB 80K+: ~5% yield → ~500~1,000 API콜 (한도 내 가능하지만 빠듯)
  - DB 90K 근처: 1,000건/일 목표 하향 조정 필요 → 남은 미수집 영역 기준으로 재설정

---

## 3. 수집 효율 최적화

### 3-1. 기존 구현 (유지)

- in-memory ISBN set: 세션 내 중복 방지
- batch_collection_state 테이블: 완료된 소스 스킵, 중단/재시작 이어하기
- is_non_book 필터: 문제집/수험서 제외
- clean_title: 제목 정제 (특별판/굿즈 정보 제거)
- 일일 API 한도 자동 중단 (4,900콜)

### 3-2. 추가 구현 필요

**yield rate 기반 스마트 스킵:**
- API 응답 50건 중 새 책 비율(yield rate) 추적
- yield rate < 10% (50건 중 새 책 5건 미만) → 해당 소스 완료 처리, 다음 소스로 이동
- 기존 "새 책 0건이면 종료"보다 API 낭비 감소

**라운드로빈 카테고리 순회:**
- 현재: 카테고리 1 끝까지 → 카테고리 2 → ...
- 변경: 카테고리 1 p1 → 카테고리 2 p1 → ... → 카테고리 17 p1 → 카테고리 1 p2 → ...
- 장르 편중 방지 → 벡터 공간의 장르 균형 유지

**새 책 기준 카운팅:**
- Daily Batch의 1,000건 목표: DB에 새로 저장된 건수 기준
- 중복 스킵한 건은 카운트하지 않음

---

## 4. 2-Tier 임베딩 전략

### 4-1. Tier 1 — 즉시 기본 임베딩

- **대상**: 수집된 모든 책 (Seed, Daily Batch, Demand Layer 캐싱 모두)
- **입력**: `title + author + genre + description` 조합 텍스트
- **모델**: OpenAI text-embedding-3-small (1536차원)
- **시점**: 수집 직후 자동 (Daily Batch, Demand Layer 비동기 큐)
- **비용**: 90K권 기준 ~$1 미만
- **목적**: 모든 책이 최소한의 벡터를 가져서 추천 대상에 즉시 포함
- **한계**: 장르/저자 수준의 구분은 가능하지만, 취향의 세부 차원(캐릭터, 문체, 세계관 등)은 부족

### 4-2. Tier 2 — AI 강화 임베딩

- **대상**: 우선순위에 따라 점진적 처리
  1. SalesPoint 상위 도서
  2. description이 빈약한 도서 (최소 품질 기준 미달)
  3. 유저 피드백이 달린 도서
- **방식**: `/book-enrichment` 스킬 실행 → 병렬 서브에이전트로 배치 처리
- **분석 내용**: 6개 취향 차원
  - 캐릭터(character), 문체(writing_style), 세계관(worldbuilding)
  - 플롯(plot), 메시지(message), 분위기(atmosphere)
- **저장**: `books.enriched_description` 컬럼에 분석 텍스트 저장
- **enriched_description 포맷**: 구조화된 prose (JSON 아님)
  ```
  [캐릭터] 주인공 OOO는 ... 성장형/몰락형/관찰자형 등의 특성을 보인다.
  [문체] 간결하고 건조한 문체로 ... 1인칭/3인칭 시점.
  [세계관] 현실 기반 / 판타지 / SF 등. 시대적 배경은 ...
  [플롯] 성장 서사 / 미스터리 / 로맨스 등. 전개 속도와 구조.
  [메시지] 핵심 주제와 메시지. 사회적/철학적/감성적 방향.
  [분위기] 따뜻한/어두운/유머러스/서정적 등 전반적 톤.
  ```
- **Tier 2 임베딩 입력**: `title + author + genre + enriched_description` (원본 description 대체)
- **임베딩**: 강화된 텍스트로 재임베딩 → `book_embeddings` 벡터 덮어쓰기
  - Tier 1 벡터는 별도 보관하지 않음 (Tier 2가 상위 호환이므로 의도적 덮어쓰기)
- **비용**: Claude Code 구독으로 해결 (추가 API 비용 0)
- **처리량**: 세션당 200~500권 (병렬 서브에이전트 5개, 1라운드 ~10분)

### 4-3. 처리량 예상

| 기간 | 대상 | 세션 수 | 효과 |
|------|------|---------|------|
| 1주 | 상위 1,000권 | ~5회 | 추천 핵심 도서 고품질화 |
| 1개월 | 상위 5,000권 | ~25회 | 온보딩 + 추천 풀 충분 |
| 2개월 | 상위 10,000권 | ~50회 | 벡터 공간 고밀도 달성 |

### 4-4. 강화 스킬 설계

```
/book-enrichment 실행 시:
  1. DB에서 미강화 책 조회 (enriched_description IS NULL, SalesPoint DESC)
  2. 병렬 서브에이전트 5개 디스패치 (각 20권)
  3. 각 에이전트: 책 분석 → enriched_description 저장 → Tier 2 임베딩 생성
  4. 1라운드(100권) 완료 → 다음 라운드 자동 진행
  5. 진행 상태 DB 기록 (이어하기 가능)
```

---

## 5. 자동화

### 5-1. GitHub Actions (Daily Batch + Tier 1 임베딩)

```yaml
# .github/workflows/daily-batch.yml
schedule: "0 3 * * *"  # 매일 새벽 3시 (KST)

steps:
  1. Python 환경 세팅
  2. smart_batch_collector.py 실행 (새 책 1,000건 목표)
  3. tier1_embedder.py 실행 (새로 수집된 책 임베딩)
  4. 실행 로그 자동 보관
```

**secrets 관리:**
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`: DB 접근
- `ALADIN_TTB_KEY`: 알라딘 API
- `OPENAI_API_KEY`: Tier 1 임베딩

**실패 대응:**
- 수집 실패: 상태 추적 덕분에 다음 날 자동 이어서 수집
- 임베딩 실패: book_embeddings에 row 없는 책 = 다음 실행 시 재처리
- GitHub Actions 실패 알림 (이메일 기본 제공)

### 5-2. 수동 트리거 (Tier 2 강화)

- `/book-enrichment` 스킬로 Eden이 수동 실행
- 매일 Claude Code 사용 시 자연스럽게 처리
- 자동화 불필요 (Claude Code 세션 내에서 동작)

---

## 6. DB 스키마 변경

### 6-1. books 테이블 — 컬럼 추가

```sql
ALTER TABLE books ADD COLUMN sales_point INT;
-- 알라딘 SalesPoint (판매량 지표). Tier 2 강화 우선순위 결정에 사용.
-- NULL이면 SalesPoint 미수집 (Demand Layer 캐싱 등).

ALTER TABLE books ADD COLUMN enriched_description TEXT;
-- AI 강화 분석 텍스트. Tier 2 임베딩의 입력으로 사용.
-- NULL이면 미강화 상태.

ALTER TABLE books ADD COLUMN updated_at TIMESTAMPTZ DEFAULT now();
-- enriched_description 업데이트 추적용.
```

### 6-2. book_embeddings 테이블 — 컬럼 추가

```sql
ALTER TABLE book_embeddings ADD COLUMN tier SMALLINT DEFAULT 1;
-- 1: 기본 임베딩 (title+author+genre+description)
-- 2: AI 강화 임베딩 (enriched_description 기반)

ALTER TABLE book_embeddings ADD COLUMN updated_at TIMESTAMPTZ DEFAULT now();
-- Tier 업그레이드 시점 추적. created_at은 최초 생성, updated_at은 마지막 갱신.
```

### 6-3. pgvector 인덱스

```sql
CREATE INDEX idx_book_embeddings_hnsw
  ON book_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
-- HNSW: 리빌드 불필요, recall 높음, 벡터 업데이트 자동 반영
-- m=16, ef_construction=64: 90K 규모에서 recall/성능 균형 기본값
```

### 6-4. batch_collection_state — Daily Batch 상태 리셋

Daily Batch는 매일 반복 실행되므로, 상태 추적 방식 명확화:
- Phase 1 (ItemList): 소스 조합(카테고리×QueryType)이 `completed=true`면 **영구 스킵** (리스트가 크게 변하지 않음)
- Phase 2-3 (검색): 검색 결과는 시간에 따라 변하므로, **30일 경과 시 completed 리셋** → 재수집 허용
- 리셋 로직: Daily Batch 시작 시 `completed=true AND updated_at < now() - 30 days` → `completed=false`로 업데이트

### 6-5. 참고: 배치 스크립트는 반드시 service role key 사용

`SUPABASE_SERVICE_ROLE_KEY`는 RLS를 우회한다. 배치 수집/임베딩 스크립트는 반드시 이 키를 사용해야 한다. anon key로는 books/book_embeddings INSERT가 차단됨.

---

## 7. Scalability 검증

| 항목 | 규모 | 판단 |
|------|------|------|
| 90K rows PostgreSQL | 매우 작음 | 문제 없음 |
| 90K × 1536-dim vectors (pgvector) | ~550MB | HNSW 인덱스로 충분 |
| OpenAI Tier 1 임베딩 비용 | 90K × ~500토큰 = ~$1 | 무시 가능 |
| enriched_description 저장 | ~50~100MB | 문제 없음 |
| Tier 2 강화 비용 | Claude Code 구독 | 추가 비용 0 |
| Daily Batch API 소비 | ~1,000~2,000콜/일 (한도 5,000) | 여유 있음 |

---

## 8. 수집 전략 진화 로드맵

### Stage 1 — 출시 전~초기 (현재)

- **수집 기준** (업데이트 2026-04-16):
  - 메인 발견: **정보나루 loanItemSrch/recommandList/srchBooks** (실제 독서 데이터)
  - 정합성: 모든 신규 ISBN 은 **usageAnalysisList** 후처리로 `loan_count` (누적) + `loan_count_12mo` (최근) 통일 저장
  - 보완: **알라딘 Bestseller/ItemNew** — 완전 초기 신간 커버 (정보나루 6~12개월 지연)
  - fallback_curation 랭킹은 두 소스 Strategy C 로 혼합 (상세: `2026-04-16-data4library-aladin-hybrid-collection.md`)
- Seed Layer 1회 + Daily Batch 매일 자동
- 목표: ~20,000권 DB + Tier 1 임베딩 100% 커버
- Tier 2 강화: `loan_count_12mo` 상위부터 점진적 처리 (실제 최근 독서 기준)

### Stage 2 — 유저 데이터 축적 후

- **수집 기준**: 자체 수요 데이터 (유저 행동 분석)
- Demand Layer 미스 패턴 분석 → 수집 우선순위 반영
- 유저 서재의 장르/저자 분포 → 해당 영역 우선 확장
- Tier 2 강화 대상: 유저 피드백 많은 책 우선

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 유저 검색/등록 │ ──▶ │ 수요 패턴 분석 │ ──▶ │ 타겟 수집     │
│ (Demand Layer)│     │ (장르/저자/   │     │ (해당 영역    │
│              │     │  키워드 분포)  │     │  깊이 확장)   │
└──────────────┘     └──────────────┘     └──────────────┘
      ↑                                          │
      └──────────────────────────────────────────┘
                  DB 커버리지 향상 → 미스율 감소
```

---

## 9. 파일 구조

```
scripts/
  smart_batch_collector.py       # 3-Phase 수집 오케스트레이터 (기존, 수정)
  tier1_embedder.py              # Tier 1 임베딩 생성기 (신규)
  lib/
    aladin_client.py             # API 클라이언트 (기존)
    book_filter.py               # 문제집 필터 (기존)
    title_cleaner.py             # 제목 정제 (기존)
    state_manager.py             # 상태 추적 (기존)
  data/
    search_keywords.json         # Phase 2-3 키워드 (기존)

.github/
  workflows/
    daily-batch.yml              # GitHub Actions 자동화 (신규)

supabase/
  001_init_schema.sql            # 코어 스키마 (기존)
  002_batch_state.sql            # 상태 추적 (기존)
  003_embedding_schema.sql       # 임베딩 관련 스키마 변경 (신규)
```

---

## 10. 기존 문서 업데이트 필요

| 문서 | 변경 내용 |
|------|----------|
| `docs/ARCHITECTURE.md` | 3-Layer 구조 반영, 2-Tier 임베딩 전략 추가, Demand Layer 검색 흐름 추가, `books.source` 값 `google_books` → `kakao`로 수정 |
| `docs/plans/batch-collection-agent.md` | 이 스펙으로 대체 (Phase 2-3 삭제 → Daily Batch로 통합, 스킬 설계 업데이트) |
