# v3 미처리 데이터 생성 설계

> 날짜: 2026-04-02
> 상태: 설계 완료
> 선행 문서: `docs/superpowers/specs/2026-04-01-recommendation-engine-v3-design.md`

---

## 1. 목적

v3 추천 엔진이 작동하려면 모든 대상 책(rich_description 보유, 2,505권)에 4종류 벡터가 필요하다. 현재 reason만 부분 존재하고, desc/L1/L2는 전무. 이 문서는 미처리 데이터를 생성하고 저장하는 전체 설계를 정의한다.

| 데이터 | 현재 | 필요 | 갭 |
|--------|------|------|-----|
| reason (book_love_reasons, llm_extracted) | 1,748권 / 19,450건 | 2,505권 | 757권 |
| desc 임베딩 (2000D) | 0 | 2,505권 | 2,505권 (신규) |
| L1 장르 임베딩 (2000D) | 0 | 고유 ~20개 → 2,505권 매핑 | 전체 (신규) |
| L2 장르 임베딩 (2000D) | 0 | 고유 ~300개 → 2,505권 매핑 | 전체 (신규) |

---

## 2. DB 스키마

### 2.1 genre_embeddings (신규 테이블)

고유 장르 텍스트의 임베딩. L1 ~20개 + L2 ~300개 = 약 320행.

```sql
CREATE TABLE genre_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  genre_text TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('l1', 'l2')),
  embedding vector(2000) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(genre_text, level)
);
```

### 2.2 book_v3_vectors (신규 테이블)

책별 desc 임베딩 + L1/L2 장르 FK. 대상: rich_description 보유 책.

```sql
CREATE TABLE book_v3_vectors (
  book_id UUID PRIMARY KEY REFERENCES books(id),
  desc_embedding vector(2000),
  source_text TEXT,              -- desc 임베딩에 사용된 원본 텍스트 (디버깅용)
  l1_text TEXT,                  -- 파싱된 L1 장르 텍스트 (디버깅용)
  l2_text TEXT,                  -- 파싱된 L2 장르 텍스트 (디버깅용)
  l1_genre_id UUID REFERENCES genre_embeddings(id),
  l2_genre_id UUID REFERENCES genre_embeddings(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()  -- INSERT 시 자동. UPDATE 시 트리거 또는 앱 코드에서 갱신 필요.
);

CREATE INDEX idx_book_v3_l1 ON book_v3_vectors(l1_genre_id);
CREATE INDEX idx_book_v3_l2 ON book_v3_vectors(l2_genre_id);
```

### 2.3 기존 테이블 (변경 없음)

- `book_love_reasons`: reason 임베딩 저장 (vector(2000), llm_extracted). 미처리 757권 추가. 참고: ARCHITECTURE.md에 3072D로 기재되어 있으나 실측 2000D — 구현 시 ARCHITECTURE.md 업데이트 필요.
- `book_embeddings`: tier1/tier2 (1536D). book-to-book 유사도용. 이 작업과 무관.

---

## 3. 데이터 생성 규칙

### 3.1 장르 파싱 (L1/L2 분리)

```
원본: "국내도서>소설/시/희곡>한국소설>2000년대 이후 한국소설"
→ parts = ["국내도서", "소설/시/희곡", "한국소설", "2000년대 이후 한국소설"]
→ 접두어 제거: parts[0]이 "국내도서", "외국도서", "eBook"이면 제거
→ L1 = parts[1] = "소설/시/희곡"
→ L2 = " ".join(parts[2:]) = "한국소설 2000년대 이후 한국소설"
```

**L2는 나머지 전부 이어붙임.** 깊은 장르일수록 더 특화된 L2를 가진다. v3 검증에서 같은 소분류 내 유사도 0.970 확인됨 — "한국소설"과 "한국소설 2000년대 이후"는 충분히 가깝되, 미세 취향 구분이 가능.

**엣지케이스:**
- genre가 빈 문자열 또는 NULL (1건 확인) → book_v3_vectors에 desc_embedding만 저장, L1/L2 = NULL
- depth 2 이하 (현재 0건이지만 방어) → L2 = NULL

### 3.2 desc 임베딩 소스 텍스트

```
소스 = clean_html(rich_description).strip()
if not 소스 or len(소스) < 200:
    소스 = f"{title} ({genre}) — {description}"
소스 = 소스[:2000]
```

- `clean_html`: HTML 태그 제거 (`re.sub(r'<[^>]+>', '', text)`)
- 1순위: rich_description에서 HTML 제거 후 최대 2000자
- 폴백: clean 결과가 빈 문자열이거나 200자 미만이면 메타데이터 조합
- v3 검증 기준: "2000자가 정답1위 6/7, 200자는 5/7"

### 3.3 reason 추출 (기존 파이프라인, 일부 수정)

미처리 757권에 대해 safe_rerun.py 실행.
- 2-stage LLM 추출 (gpt-4o-mini, temperature=0)
- text-embedding-3-large 2000D 임베딩
- source='llm_extracted'로 book_love_reasons에 저장

**safe_rerun.py 수정 필요 사항:**
현재 safe_rerun.py에는 배치 운영 규칙(4.2) 중 "연속 에러 3회 → 자동 중단" 로직이 없음.
실행 전에 아래를 추가해야 함:
- 연속 에러 카운터 + 3회 시 즉시 중단 + 상태 보고
- 429 응답 시 response body 확인 (rate limit vs quota 구분)

**기존 reason 품질 이슈:**
76권이 reason 5개 미만 (1개: 6권, 2개: 19권, 3개: 25권, 4개: 26권).
v3 avg_maxsim은 reason이 적으면 불안정하므로, 검증 시 5개 미만인 책을 플래그해야 함 (섹션 7 참조).

### 3.4 임베딩 모델

모든 신규 임베딩: **text-embedding-3-large, 2000차원** (Matryoshka 축소)
기존 book_love_reasons.reason_embedding과 동일 모델/차원.
설정: `scripts/lib/openai_helpers.py`의 EMBEDDING_MODEL, EMBEDDING_DIMENSIONS

---

## 4. 배치 스크립트 설계

### 4.1 실행 순서 (의존성 기반)

```
1. genre_embeddings 생성    (선행 조건 없음, ~320개 API 호출)
2. book_v3_vectors 생성      (genre_embeddings 완료 후, ~2,505개 API 호출)
3. reason 추출               (독립, ~757권 × LLM + 임베딩)
```

1과 3은 병렬 실행 가능. 2는 1 완료 후.

### 4.2 배치 운영 규칙 (feedback_batch_operations.md 준수)

모든 배치 스크립트는 아래 6항목 필수:

1. **배치 간 sleep**: 최소 1초 (임베딩 API), LLM은 2초
2. **연속 에러 N회 → 자동 중단**: max 3회 연속 에러 시 즉시 중단 + 상태 보고
3. **중간 체크포인트**: 100건마다 진행상황 저장 (재시작 시 이어서 처리)
4. **사전 테스트**: 1건 먼저 실행, 성공 확인 후 전체 배치
5. **실패 시 즉시 중단 + 상태 보고**: 의미 없는 재시도 금지
6. **rate limit 자동 체크**: 429 응답 시 대기 후 재시도 (response body 확인)

### 4.3 스크립트 구조

```
scripts/
├── generate_genre_embeddings.py    # genre_embeddings 테이블 생성
├── generate_book_v3_vectors.py     # book_v3_vectors 테이블 생성
└── safe_rerun.py                   # reason 추출 (연속 에러 중단 로직 추가 필요)
```

모든 신규 스크립트는 `scripts/lib/openai_helpers.py`를 임포트하여 모델/차원 설정을 공유한다.

**generate_genre_embeddings.py:**
1. books 테이블에서 고유 genre 텍스트 수집
2. L1/L2 파싱 → 고유 (genre_text, level) 쌍 추출
3. 기존 genre_embeddings에 없는 것만 필터
4. 1건 테스트 → 전체 배치 (20개씩 임베딩, 1초 sleep)
5. INSERT INTO genre_embeddings

**generate_book_v3_vectors.py:**
1. rich_description 보유 books 조회
2. 기존 book_v3_vectors에 없는 것만 필터 (safe rerun)
3. 각 책에 대해:
   - genre 파싱 → L1/L2 텍스트 → genre_embeddings에서 FK 조회
   - desc 소스 텍스트 생성 (3.2 규칙)
4. 1건 테스트 → desc 임베딩 배치 (20개씩, 1초 sleep)
5. INSERT INTO book_v3_vectors (desc_embedding + L1/L2 FK)

---

## 5. FastAPI 서버 로딩

서버 시작 시 벡터 로드 (2개 쿼리):

**desc/L1/L2 로드:**
```sql
SELECT v.book_id, v.desc_embedding,
       g1.embedding AS l1_embedding,
       g2.embedding AS l2_embedding
FROM book_v3_vectors v
LEFT JOIN genre_embeddings g1 ON v.l1_genre_id = g1.id
LEFT JOIN genre_embeddings g2 ON v.l2_genre_id = g2.id;
```

**reason 로드 (별도):**
```sql
SELECT book_id, reason, reason_embedding
FROM book_love_reasons
WHERE source = 'llm_extracted';
```

- LEFT JOIN: L1/L2 없는 책도 desc만으로 추천 가능
- NULL L1/L2 → 해당 스코어 = 0 (v3 공식에서 자연스럽게 처리)
- 벡터 인덱스(HNSW/IVFFlat) 불필요: v3 엔진은 numpy 메모리 연산, pgvector 검색 안 함

---

## 6. 일일 배치 연동

신규 책이 매일 추가되므로, 기존 daily 배치 파이프라인에 통합:

```
03:00  daily-collect         (새 책 수집)
       daily-scrape          (2시간마다, rich_description 수집)
05:00  daily-extract-reasons (reason 추출 — safe_rerun.py)
05:00  daily-v3-vectors      (신규: desc/L1/L2 생성, reason과 독립이므로 병렬 가능)
06:30  daily-embed-t2        (tier2 임베딩)
07:00  daily-taste-recompute (취향 재계산)
```

참고: daily-v3-vectors는 reason 추출과 의존성이 없다 (desc/L1/L2만 생성). 따라서 동시 실행 가능.

**운영 단계 TODO (초기 배치 이후):** 기존 책의 rich_description이나 genre가 변경된 경우를 감지하는 로직 필요. `books.updated_at > book_v3_vectors.updated_at`인 책을 찾아 desc/FK를 재생성하는 방식. 초기 배치에서는 대상 아님.

daily-v3-vectors:
1. books에서 rich_description NOT NULL이면서 book_v3_vectors에 행 없는 책 조회
2. 새 장르가 있으면 genre_embeddings에 추가
3. desc 임베딩 생성 + FK 매핑 → book_v3_vectors INSERT

---

## 7. 검증 체크리스트

배치 완료 후 확인할 항목:

1. **커버리지**: book_v3_vectors 행 수 = rich_description 보유 books 수
2. **NULL 체크**: desc_embedding이 NULL인 행 = 0 (정상이면)
3. **FK 정합성**: l1_genre_id/l2_genre_id가 genre_embeddings에 존재
4. **L1/L2 분포**: 고유 L1 ~20개, L2 ~300개 범위 내
5. **reason 커버리지**: book_love_reasons의 distinct book_id ≥ 2,400
6. **reason 품질**: reason 5개 미만인 책 목록 추출 (현재 76권). 4개 이하인 책은 재추출 후보로 플래그
7. **임베딩 차원**: 모든 벡터가 2000차원
8. **서버 로딩 테스트**: JOIN 쿼리 실행 → 결과 행 수 = book_v3_vectors 행 수

---

## 8. API 비용 추정

| 작업 | 호출 수 | 모델 | 추정 비용 |
|------|---------|------|----------|
| genre 임베딩 (~320개) | ~16 배치 (20개씩) | text-embedding-3-large | ~$0.01 |
| desc 임베딩 (~2,505개) | ~126 배치 | text-embedding-3-large | ~$0.10 |
| reason LLM (~757권) | ~757 호출 | gpt-4o-mini | ~$0.50 |
| reason 임베딩 (~5,000건, 757권 × ~6.6개) | ~250 배치 | text-embedding-3-large | ~$0.05 |
| **합계** | | | **~$0.66** |

---

## 9. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| OpenAI rate limit | 배치 중단 | 1초 sleep + 429시 대기 + 재시도 |
| reason 추출 품질 저하 | 추천 품질 하락 | 기존 검증된 프롬프트 사용, 100권마다 체크포인트 |
| genre 포맷 예외 | FK 매핑 실패 | 빈 genre skip + 로그, 파싱 실패 시 L1/L2 = NULL |
| Supabase 연결 끊김 | 배치 중단 | 3회 재시도 + 중간 저장으로 이어서 처리 |
| rich_description 200자 미만 | desc 품질 저하 | 메타데이터 폴백 (title + genre + description) |
