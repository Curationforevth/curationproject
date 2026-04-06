# v3 파이프라인 수정 + 도서 보강 진단

> 2026-04-06 | Eden

## 1. 배경

v3 추천 엔진용 데이터 생성 스크립트 4개(`generate_genre_embeddings.py`, `generate_book_v3_vectors.py`, `v3_reason_extract.py`, `verify_v3_data.py`)가 준비되어 있으나 아직 실행되지 않았다. 코드 리뷰 결과 안정성 이슈가 다수 발견되어, 수정 후 배치를 실행한다.

추가로, 전체 8,610권 중 rich_description 보유 책은 2,510권(29%)이다. YES24 스크래핑이 자연스럽게 풀을 확장하지만, 매칭 실패로 영구적으로 못 채우는 책이 존재한다. **추천 풀은 rich_description 있는 책만 사용**하되, YES24 매칭 실패 원인을 진단하여 커버리지 개선 가능성을 측정한다.

---

## 2. Part 1 — v3 스크립트 버그 수정

### 2.1 generate_genre_embeddings.py

| # | 이슈 | 수정 |
|---|------|------|
| 1 | batch INSERT 실패 시 통째로 스킵 | 개별 1건씩 재시도 fallback 추가 (generate_book_v3_vectors.py 패턴 참고) |
| 2 | dimension 하드코딩 `== 2000` | `openai_helpers.EMBEDDING_DIMENSIONS` 상수 사용 |
| 3 | 429 rate limit 미구분 | `retry.py`의 backoff가 이미 429를 핸들링하므로, embedding 호출부를 `with_retry()` 래핑 |

### 2.2 generate_book_v3_vectors.py

| # | 이슈 | 수정 |
|---|------|------|
| 1 | pagination `.range(offset, offset+499)` | `.range(offset, offset+999)` 로 변경 (Supabase 기본 limit 1000) |
| 2 | 체크포인트 로그만 찍고 상태 파일 없음 | 처리 완료 book_id 목록을 JSON 파일로 저장 (`scripts/.checkpoint_book_v3.json`), 재시작 시 스킵 |
| 3 | embedding API 실패 시 배치 통째로 스킵 | 개별 재시도 fallback 추가 (INSERT와 동일 패턴) |

### 2.3 v3_reason_extract.py

| # | 이슈 | 수정 |
|---|------|------|
| 1 | 임포트 경로 `from lib.openai_helpers` | 실행 환경에 맞는 경로로 수정 (다른 스크립트와 통일) |
| 2 | pagination `.range(offset, offset+499)` | `.range(offset, offset+999)` 로 변경 |
| 3 | ThreadPoolExecutor 타임아웃 없음 | `future.result(timeout=60)` 추가 |
| 4 | 임베딩 실패 시 silent drop (로그 없이 버림) | 실패 건수 + book_id 로깅 추가 |
| 5 | INSERT fallback 5-row 청크 | 1건씩 개별 재시도로 변경 |
| 6 | 체크포인트 상태 파일 없음 | 처리 완료 book_id 목록을 JSON 파일로 저장 (`scripts/.checkpoint_v3_reason.json`), 재시작 시 스킵 |
| 7 | 체크포인트 조건 `total_done % CHECKPOINT_INTERVAL < CHUNK_SIZE` 가 fragile | `total_done % CHECKPOINT_INTERVAL == 0` 으로 단순화 |

### 2.4 verify_v3_data.py

| # | 이슈 | 수정 |
|---|------|------|
| 1 | source 필터 `"llm_extracted"` 만 조회 | `.in_("source", ["llm_extracted", "v3_context_rich"])` 로 변경 |
| 2 | pagination 루프에 try/except 없음 | 에러 핸들링 추가 |
| 3 | FK 검증 없음 | `l1_genre_id`, `l2_genre_id`가 `genre_embeddings`에 실제 존재하는지 검증 추가 |
| 4 | 커버리지 기준 99% 너무 느슨 | 99.5% 이상으로 상향 |
| 5 | 벡터 dimension 검증 없음 | 샘플 5건 추출하여 2000D 확인 추가 |

### 2.5 공통

- **체크포인트 파일 위치**: `scripts/.checkpoint_*.json`
- **체크포인트 파일 포맷**: `{"done_ids": [...], "last_updated": "ISO timestamp"}`
- **재시작 로직**: 스크립트 시작 시 체크포인트 파일 존재하면 done_ids를 로드하여 해당 book_id 스킵
- **`state_manager.py`는 사용하지 않음**: Supabase 테이블 기반이라 오버헤드가 큼. 로컬 JSON이 단순하고 충분함

---

## 3. 배치 실행 계획

### 3.1 실행 순서 (의존관계)

```
1. generate_genre_embeddings.py   (~320건, ~10분)
   ↓ genre_embeddings 테이블 채워야 FK 참조 가능
2. generate_book_v3_vectors.py    (~2,510건, ~1시간)
   ↓ 독립 실행 가능
3. v3_reason_extract.py           (~757건, ~30분)  ← 2번과 병렬 가능
   ↓
4. verify_v3_data.py              (검증, ~2분)
   ↓
5. build_index.py                 (index.pkl 재빌드)
   ↓
6. Render 서버 재배포             (git push → 자동 빌드)
```

### 3.2 실행 전 체크리스트

- [ ] `.env` 파일에 `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` 확인
- [ ] 각 스크립트 `--dry-run` 또는 pre-test 1건 통과 확인
- [ ] 예상 API 비용: ~$0.66 (genre $0.01 + desc $0.10 + reason LLM $0.50 + reason embed $0.05)

### 3.3 실행 확인 기준 (100% 확신 조건)

배치 실행 전, 모든 수정이 완료되었음을 아래로 검증:

1. 각 스크립트의 pre-test (1건) 성공
2. verify_v3_data.py가 기존 데이터에 대해 에러 없이 실행됨
3. 체크포인트 파일 저장/로드 동작 확인 (수동 테스트)

---

## 4. Part 2 — YES24 매칭 진단 테스트

### 4.1 목적

rich_description IS NULL인 책들의 YES24 매칭 실패 원인을 정량적으로 파악하여, 스크래퍼 개선이 의미 있는지 판단한다.

### 4.2 진단 스크립트 설계

**파일명**: `scripts/yes24_match_diagnostic.py`

**동작**:
1. `books` 테이블에서 `rich_description IS NULL AND isbn IS NOT NULL` 인 책 중 **300권 샘플링**
   - 랜덤이 아닌 stratified: 비표준 ISBN 50권 + 일반 ISBN 250권 (비표준 비율 측정 포함)
2. 각 책에 대해 기존 `yes24_scraper.py`의 매칭 로직을 dry-run
3. 실패 원인 분류:

| 코드 | 의미 |
|------|------|
| `success` | 매칭 성공 (이전 일시적 오류였을 가능성) |
| `not_found` | YES24 검색 결과 0건 |
| `isbn_mismatch` | 검색 결과는 있지만 ISBN 불일치 |
| `non_standard_isbn` | K-prefix 등 비표준 ISBN |
| `no_content` | 페이지 찾았지만 콘텐츠 섹션 없음 |

4. 결과를 CSV 저장 + 터미널 요약 출력

**속도**: 300권 × 1초 딜레이 = ~5분, API 비용 $0

### 4.3 판단 기준

진단 결과를 보고 아래 기준으로 다음 액션 결정:

| 실패 유형 | 비율 기준 | 액션 |
|-----------|----------|------|
| `isbn_mismatch` ≥ 20% | ISBN 양방향 변환 + fuzzy title 매칭 추가 |
| `not_found` ≥ 20% | 검색 쿼리 변형 로직 추가 (부제 제거, 저자 성만) |
| `non_standard_isbn` ≥ 10% | title-only 검색 모드 추가 |
| `no_content` ≥ 10% | 추가 HTML 섹션 탐색 |
| `success` ≥ 15% | 단순 재실행만으로도 커버리지 향상 |
| 모든 유형 < 10% | 현상 유지 (개선 ROI 낮음) |

복수 조건 해당 시 비율 높은 순서로 우선 처리.

---

## 5. 범위 외 (하지 않는 것)

- 교보문고 등 추가 스크래핑 소스 (진단 결과 보고 결정)
- rich_description 없는 책의 fallback 벡터 생성 (추천 풀에서 제외하기로 함)
- 추천 엔진 로직 변경 (데이터 파이프라인 수정만)
- daily cron에 v3 생성 추가 (일회성 백필 완료 후 별도 논의)
