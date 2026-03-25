# 정보나루 API 연동 설계

> 도서관 정보나루(data4library.kr) `usageAnalysisList` API로 도서별 키워드 + 함께 빌린 책 수집.
> 키워드는 Tier2 임베딩에 즉시 활용, 연관도서는 Phase 3 추천 엔진용으로 저장.

---

## 1. 목적

| 목적 | 활용 시점 | 데이터 |
|------|-----------|--------|
| 임베딩 품질 향상 | 지금 (Phase 1) | `library_keywords` → `compose_embedding()`에 추가 |
| 추천 그래프 기반 데이터 | Phase 3 | `related_isbns` (co_loan) → 추천 엔진 보조 신호 |

## 2. API 설계

### 사용 엔드포인트

`usageAnalysisList` **1콜**로 키워드 + 함께 빌린 책 동시 수집.

```
GET http://data4library.kr/api/usageAnalysisList
  ?authKey={DATA4LIBRARY_API_KEY}
  &isbn13={isbn}
  &format=json
```

### 사용하지 않는 엔드포인트

| 엔드포인트 | 사유 |
|------------|------|
| `keywordList` | `usageAnalysisList`에 키워드 포함, 별도 호출 불필요 |
| `recommandList` (mania/reader) | 추가 2콜 필요, Phase 3 설계 시 재평가 |

### Rate Limit

- 일일 30,000건
- 1콜/권 → 일일 최대 30,000권 처리 가능
- 백필(8,589권): 1일 이내 완료
- 일일 운영(신규 ~300권): 한도 대비 1% 수준

## 3. DB 스키마 변경

Supabase 마이그레이션 007번.

### `books` 테이블 컬럼 추가

| 컬럼 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `library_keywords` | `text[]` | 정보나루 키워드 | `{"인생", "성장", "자아찾기"}` |
| `related_isbns` | `jsonb` | 함께 빌린 책 ISBN 목록 | `{"co_loan": ["9788932920...", ...]}` |

- `library_keywords`: `mood_tags text[]`와 동일 패턴. `compose_embedding()`에서 바로 사용.
- `related_isbns`: co_loan만 저장. 타입별 최대 50개 cap. jsonb wrapper(`{"co_loan": [...]}`)는 Phase 3에서 `mania`, `reader` 타입 확장 대비. 정규화 테이블 마이그레이션 가능성 있음.

## 4. 스크립트

### `scripts/data4library_collector.py`

기존 YES24 스크래퍼와 동일한 패턴.

```
class Data4LibraryCollector:
    def __init__(self, dry_run=False)

    def fetch_usage(isbn) → (keywords: list[str], co_loan_isbns: list[str])
        # usageAnalysisList 1콜 → 키워드 + co_loan 파싱

    def collect(limit) → 처리 결과 통계
        # 대상: library_keywords IS NULL인 책
        # ISBN 기반으로 수집 → books 테이블 업데이트

    def status() → 현황 출력

CLI:
  --limit N      최대 처리 권수 (기본 300)
  --dry-run      DB 저장 없이 테스트
  --status       현황 조회
```

요청 간 딜레이: **0.5초** (yes24_scraper의 1.0초보다 짧게 — 공공 API라 봇 차단 위험 낮음). 8,589권 백필 시 예상 소요: ~72분.

### 의존성

- `requests` (이미 설치됨)
- `lib/retry.py`의 `with_retry` 재사용
- `supabase`, `dotenv` (기존)
- `requirements.txt` 변경 없음

## 5. 임베딩 연동

`scripts/tier2_embedder.py`의 `compose_embedding()` 수정:

```python
# 기존 주석 해제 + 활성화
if book.get('library_keywords'):
    parts.append(f"키워드: {', '.join(book['library_keywords'])}")
    data_sources.append('library_keywords')
```

### tier2_embedder.py 변경

**1) `fetch_books_needing_tier2()` SELECT 절 수정:**

```python
# 기존
.select("id, title, author, genre, description, rich_description")
# 변경
.select("id, title, author, genre, description, rich_description, library_keywords")
```

**2) 재임베딩 대상 확장:**

현재 로직은 "tier 2 임베딩이 없는 책"만 대상으로 삼음. 키워드 추가 후 재임베딩이 필요한 책도 포함해야 함:

```python
# 기존: tier2_ids에 있으면 스킵
books = [b for b in all_books if b["id"] not in tier2_ids]

# 변경: tier2_ids에 있더라도, data_sources에 'library_keywords' 없고
#       library_keywords가 있는 책은 재임베딩 대상에 포함
needs_reembed = set()  # data_sources에 library_keywords 없는 tier2 book_ids
# book_embeddings에서 tier=2이고 data_sources NOT LIKE '%library_keywords%' 조회
books = [b for b in all_books
         if b["id"] not in tier2_ids or b["id"] in needs_reembed]
```

이렇게 하면 일일 운영에서 키워드가 새로 수집된 책이 자동으로 재임베딩 대상이 됨.

## 6. 워크플로우 통합

`daily-embed-t2.yml`에 선행 스텝으로 추가:

```yaml
# Step 1: 정보나루 키워드/연관도서 수집 (새로 추가)
- name: Collect library keywords & co-loan data
  continue-on-error: true
  env:
    SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
    SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
    DATA4LIBRARY_API_KEY: ${{ secrets.DATA4LIBRARY_API_KEY }}
  run: python scripts/data4library_collector.py --limit 300

# Step 2: Tier 2 임베딩 (기존)
- name: Run Tier 2 embedder
  ...
```

### 타이밍

```
KST 06:30  daily-embed-t2:
  1) data4library_collector (신규분 키워드/co_loan 수집)
  2) tier2_embedder (키워드 포함하여 임베딩 생성/갱신)
```

정보나루 실패 시 `continue-on-error: true`로 Tier2 임베딩은 정상 실행.

## 7. 백필 전략

1. GitHub Secrets에 `DATA4LIBRARY_API_KEY` 추가
2. 로컬에서 수동 실행: `python scripts/data4library_collector.py --limit 10000`
3. 8,589권 × 0.5초 딜레이 = ~72분 소요
4. 백필 완료 후 재임베딩: `python scripts/tier2_embedder.py --force --limit 9000`
   - rich_description이 있는 책만 대상 (현재 ~53권, YES24 수집 진행에 따라 증가)
   - OpenAI 임베딩 비용: ~53권 × ~7,500토큰 ≈ 0.004 USD (무시할 수준)

## 8. 에러 처리

| 상황 | 처리 |
|------|------|
| ISBN이 정보나루에 없음 | `library_keywords`를 빈 배열 `{}`로 설정하여 재처리 방지. 향후 정보나루에 등록될 수 있으므로 주기적 재시도는 Phase 3에서 고려 |
| API 일일 한도 초과 | HTTP 응답 확인 후 로그 남기고 중단 (구현 시 정보나루의 한도 초과 응답 코드 확인 필요) |
| 네트워크/타임아웃 | `with_retry` (exponential backoff + jitter) |
| 응답 파싱 실패 | 키워드/co_loan 각각 독립 파싱. 한쪽 실패해도 성공한 쪽만 저장 |

## 9. 모니터링

`--status` 출력 예시:
```
=== 정보나루 수집 현황 ===
전체 도서: 8,589
키워드 수집 완료: 8,200 (95.5%)
키워드 미수집: 389
연관도서 있음: 6,150 (71.6%)
```

## 10. Deliverables

- [ ] Supabase 마이그레이션 007 (library_keywords, related_isbns)
- [ ] `scripts/data4library_collector.py`
- [ ] `scripts/tier2_embedder.py` 수정 (SELECT 절 + 재임베딩 로직 + compose_embedding 주석 해제)
- [ ] `.github/workflows/daily-embed-t2.yml` 수정
- [ ] GitHub Secrets에 `DATA4LIBRARY_API_KEY` 추가
- [ ] `docs/ARCHITECTURE.md` 동기화 (파이프라인 흐름 + books 테이블 컬럼)
- [ ] 테스트
- [ ] 백필 실행

## 11. 범위 외 (Phase 3)

- `recommandList` (mania/reader) 수집
- `related_isbns` → `book_relations` 정규화 테이블 마이그레이션
- 연관도서 기반 그래프 추천 알고리즘
- 빈 배열 키워드 책 주기적 재시도
