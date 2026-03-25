# Tier 2 Embedding Pipeline Design

> **Deprecated (2026-03-25)**: 파이프라인 재설계로 워크플로우 구조 변경됨. `2026-03-25-pipeline-redesign.md` 참조.

> 점진적 임베딩 파이프라인 — 데이터가 쌓일수록 임베딩이 풍성해지는 구조

---

## 배경

현재 book_embeddings은 Tier 1(제목+저자+장르+알라딘 1줄 설명)으로만 생성된 상태. YES24 스크래핑으로 rich_description이 수집되고 있고, 도서관 정보나루 키워드도 예정돼있어 이 데이터를 임베딩에 반영하는 파이프라인이 필요하다.

### 테스트 결과 요약

7가지 임베딩 방식을 10권으로 책-책 유사도 비교 테스트한 결과:

| 방식 | 결과 |
|------|------|
| Tier 1 (기존) | 장르 구분은 되지만 얕음 |
| 책소개만 클리닝 | 장르 혼동 발생 ([4]소설 Top1이 [2]인문학) |
| LLM taste_summary | 어투 균질화로 장르 구분 흐려짐 ([0]소설 Top2가 [5]성공학) |
| LLM 태그+책소개 | 책소개만과 비슷, LLM 비용 대비 개선 없음 |
| 장르prefix+책소개 | 깔끔하지만 문체/분위기 신호 부족 |
| **책소개+책속으로 발췌** | **가장 좋음** — 소설끼리 강하게 클러스터(0.55~0.59), 이질 장르 잘 분리 |
| 전체텍스트 클리닝 | 괜찮지만 노이즈로 일부 혼동 |

## 핵심 설계: 점진적 임베딩

전략 교체 방식이 아니라, **가용한 데이터가 늘어날 때마다 임베딩이 풍성해지는 구조**.

```
compose_embedding(book):
    text = 기본 (title + author + genre)          # 항상 있음

    if 책소개:       text += 책소개 (cleaned)        # YES24 수집 후
    if 책속으로:     text += 발췌 300자              # YES24 수집 후
    if 키워드:       text += 가중치 키워드            # 도서관정보나루 승인 후
    if 피드백 집계:  text += 피드백 요약              # Phase 2

    return truncate(text, max_tokens=7500)         # 안전 마진
```

**데이터가 많을수록 임베딩이 좋아지고, 새 소스가 추가되면 해당 책만 re-embed.**

## 결정사항

| 항목 | 결정 |
|------|------|
| 현재 임베딩 소스 | 제목+저자+장르 + 책소개(cleaned) + 책속으로 발췌(300자) |
| 모델 | text-embedding-3-small (Tier 1과 동일 벡터 공간) |
| 확장 방식 | compose_embedding() 함수에 데이터 소스 추가 |
| 토큰 안전장치 | 7,500토큰에서 truncate (8,191 한도 내 안전 마진) |
| LLM 전처리 | 사용하지 않음 (테스트에서 개선 없음 확인) |

## DB 스키마 변경

### book_embeddings 컬럼 추가

마이그레이션 파일: `supabase/006_tier2_embedding.sql`

```sql
ALTER TABLE book_embeddings ADD COLUMN source_text text;
ALTER TABLE book_embeddings ADD COLUMN data_sources jsonb DEFAULT '[]'::jsonb;
```

| 컬럼 | 타입 | 용도 |
|------|------|------|
| `source_text` | text | 임베딩에 사용된 전체 텍스트 (디버깅/재현) |
| `data_sources` | jsonb | 사용된 데이터 소스 목록. 예: `["aladin", "yes24_intro", "yes24_excerpt"]` |

기존 8,539개 Tier 1 행: source_text=NULL, data_sources=NULL로 유지. 필요 시 나중에 backfill.

### data_sources 값 정의

| 값 | 의미 | 시점 |
|-----|------|------|
| `aladin` | title + author + genre + description | 항상 |
| `yes24_intro` | 책소개 섹션 | YES24 스크래핑 후 |
| `yes24_excerpt` | 책속으로 발췌 | YES24 스크래핑 후 (77%의 책에 있음) |
| `library_keywords` | 도서관 정보나루 키워드 | API 승인 후 (미래) |
| `user_feedback` | 유저 피드백 집계 | Phase 2 (미래) |

## 스크립트 구조

단일 파일: `scripts/tier2_embedder.py`

### CLI

```bash
python tier2_embedder.py                    # 미처리분 (rich_description 있고 Tier 2 아닌 책)
python tier2_embedder.py --limit 300        # 최대 300권
python tier2_embedder.py --force --limit 500  # 강제 재생성 (--limit 필수)
python tier2_embedder.py --dry-run          # DB 저장 없이 테스트
python tier2_embedder.py --status           # 현황 조회
```

### Re-embed 대상 선별

**기본 모드**: rich_description이 있고, data_sources에 'yes24_intro'가 없는 책
```sql
WHERE rich_description IS NOT NULL
  AND (book_embeddings.data_sources IS NULL
       OR NOT book_embeddings.data_sources ? 'yes24_intro')
```

**--force 모드**: rich_description이 있는 전체 책 (--limit 필수)

### 텍스트 조합 (compose_embedding)

```python
def compose_embedding(book):
    parts = []
    data_sources = ['aladin']

    # 기본 (항상)
    parts.append(f"제목: {book['title']}")
    parts.append(f"저자: {book['author']}")
    parts.append(f"장르: {book['genre']}")
    if book.get('description'):
        parts.append(f"내용: {book['description']}")

    # YES24 책소개
    intro = parse_section(book['rich_description'], '책소개')
    if intro:
        # 노이즈 제거: ★, MD 한마디, 미리보기 등
        intro = clean_intro(intro)
        parts.append(f"책소개: {intro}")
        data_sources.append('yes24_intro')

    # YES24 책속으로 발췌
    excerpt = parse_section(book['rich_description'], '책속으로')
    if excerpt:
        parts.append(f"발췌: {excerpt[:300]}")
        data_sources.append('yes24_excerpt')

    # (미래) 도서관 키워드
    # if book.get('library_keywords'):
    #     parts.append(f"키워드: {', '.join(book['library_keywords'])}")
    #     data_sources.append('library_keywords')

    text = '\n'.join(parts)
    text = truncate_to_tokens(text, max_tokens=7500)

    return text, data_sources
```

### 저장 (upsert)

```python
sb.table("book_embeddings").upsert({
    "book_id": book_id,
    "embedding": embedding,
    "tier": 2,
    "source_text": source_text,
    "data_sources": data_sources,
}, on_conflict="book_id").execute()
```

- `tier: 2`로 업데이트 (기존 tier=1 행 덮어쓰기)
- `on_conflict="book_id"`로 기존 행이 있으면 update
- Tier 2 최소 조건: `yes24_intro`가 data_sources에 포함돼야 함. 책소개 파싱 실패 시 스킵.

### parse_section / clean_intro 명세

`rich_description` 포맷 (yes24_scraper.py가 저장하는 형태):
```
[책소개]
텍스트...

[출판사리뷰]
텍스트...

[책속으로]
텍스트...
```

**parse_section(rd, section_name)**: `[{section_name}]` ~ 다음 `[` 사이 텍스트 추출

**clean_intro(text)**: 아래 패턴의 라인 제거
- `★`로 시작하는 줄
- `MD 한마디`로 시작하는 줄
- `미리보기`를 포함하는 줄
- 빈 줄 연속 정리

### truncate_to_tokens

문자 기반 근사치 사용 (한국어 ~2자 = ~1토큰). `max_tokens=7500` → `max_chars=15000`.
tiktoken 의존성 추가하지 않음.

### 에러 처리

tier1_embedder와 동일: 배치별 try/except, 에러 로그 출력 후 다음 배치 계속.

### --status 출력

```
Tier 2 임베딩 현황
==================================================
  전체 도서: 8,589권
  Tier 1 임베딩: 8,540권
  Tier 2 임베딩: 49권
  Tier 2 대기 (rich 있지만 미처리): 0권
  data_sources 분포:
    aladin + yes24_intro + yes24_excerpt: 38권
    aladin + yes24_intro: 11권
==================================================
```

### Fallback 규칙

| 케이스 | 비율 | 처리 |
|--------|------|------|
| rich_description 없음 | 99.4% (8,540/8,589) | 스킵, Tier 1 유지 |
| 책소개 있고 + 책속으로 있음 | 77% of rich | 전체 사용 |
| 책소개 있고 + 책속으로 없음 | 23% of rich | 책소개만 사용 |
| rich_description 파싱 실패 | edge case | 스킵, 에러 로그 |

## daily-enrich.yml 변경

scraper와 status 사이에 tier2_embedder 스텝 추가:

```yaml
- name: Run Tier 2 embedder
  continue-on-error: true
  env:
    SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
    SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: python scripts/tier2_embedder.py --limit 300
```

- **OPENAI_API_KEY 추가** — 이미 GitHub Secrets에 등록됨, env 블록만 추가
- **continue-on-error: true** — embedder 실패해도 status 리포트는 실행
- 타임아웃: 300권 / 50배치 = 6배치 × 0.5초 = ~3초 sleep + API 시간. 45분 내 충분

## 일일 파이프라인 전체 흐름

```
KST 03:00 — daily-batch.yml:
  1. smart_batch_collector --daily-target 1000   (알라딘 신규 수집)
  2. tier1_embedder                               (신규 책 기본 임베딩)
  3. status

KST 05:00 — daily-enrich.yml:
  1. batch_enricher --limit 200                   (색상/폰트)
  2. yes24_scraper --limit 250                    (rich_description 수집)
  3. tier2_embedder --limit 300                   (임베딩 업그레이드) ← NEW
  4. enricher status
  5. scraper status
```

신규 책 라이프사이클:
```
Day 1 03:00  알라딘에서 수집 → Tier 1 임베딩 (제목+저자+장르+1줄)
Day 1 05:00  색상/폰트 추출, YES24 스크래핑 대기열에 진입
Day N 05:00  YES24 스크래핑 완료 → Tier 2 임베딩 (+ 책소개 + 책속으로)
미래        도서관 키워드 수집 → re-embed (+ 키워드)
Phase 2     유저 피드백 집계 → re-embed (+ 피드백)
```

## 비용

| 항목 | 비용 |
|------|------|
| 현재 49권 업그레이드 | ~$0.01 |
| 매일 250권 신규 | ~$0.01/일 |
| 도서관 키워드 추가 후 전체 re-embed | ~$0.50 (1회) |

## 리스크

| 리스크 | 대응 |
|--------|------|
| 책속으로 없는 책 23% | 책소개만으로 fallback, 일관된 품질은 아님 |
| 토큰 초과 | truncate_to_tokens()으로 7,500 토큰 내 제한 |
| --force 남용 | --limit 없이 --force 실행 시 에러 발생 |
| YES24 스크래핑 속도가 병목 | 하루 250권, ~35일 소요. 병렬화나 limit 증가 검토 가능 |

## 향후 확장

1. **도서관 정보나루 키워드**: `library_keywords` 컬럼 추가 → compose_embedding에 키워드 추가 → 전체 re-embed
2. **유저 피드백 집계**: Phase 2에서 피드백 N개 이상인 책은 피드백 요약을 임베딩에 포함
3. **임베딩 품질 평가**: 유저 피드백 데이터로 추천 정확도 측정 → compose_embedding 로직 개선 근거
4. **모델 변경**: 벡터 차원이 바뀌면 전체 re-embed 필요 (book_embeddings 스키마 변경 포함)
