# 파이프라인 재설계 — 워크플로우 구조 + 스크립트 개선

> 2026-03-25 | Status: Draft

## 배경

### 오늘 발생한 장애

1. **daily-batch (failure)**: `state_manager.get_state()` 호출 시 Supabase 502 Bad Gateway → 전체 수집 파이프라인 크래시
2. **daily-enrich (cancelled)**: YES24 스크래퍼가 45분 타임아웃으로 강제 취소 → Tier 2 임베딩 미실행

### 구조적 문제

| # | 문제 | 영향 |
|---|------|------|
| 1 | Supabase 호출에 retry 없음 | 일시적 502/503 → 전체 실패 |
| 2 | YES24 스크래퍼가 Playwright(Firefox) 사용 | 45분에 ~60권. 250권 목표 달성 불가 |
| 3 | 워크플로우 cancel이 downstream 전파 | scraper 타임아웃 → tier2 embedder 미실행 |
| 4 | 스크래핑 트래픽 집중 | 짧은 시간에 대량 요청 → IP 차단 리스크 |
| 5 | 색상/폰트 보강이 불필요하게 분리 | 100% 완료 상태인데 별도 워크플로우 |

### 현황 (2026-03-25)

- 전체 도서: 8,589권
- 색상/폰트 보강: 100% 완료
- rich_description: 191권 (2%) — 8,398권 미완료
- Tier 2 임베딩: rich_description 있는 도서만 대상

---

## 검증 결과

YES24를 requests+BeautifulSoup으로 교체하는 PoC를 50권으로 테스트:

| 지표 | Playwright (현재) | requests+BS4 (개선) |
|------|-------------------|---------------------|
| 성공률 | ~78% (추정) | **92% (46/50)** |
| 속도 | ~45분/60권 | **121초/50권 (2.4초/권)** |
| 250권 소요 | 45분+ (타임아웃) | **~10분** |
| ISBN 매칭 | 첫 번째 결과만 | 상위 5건 순회 → 불일치 20%→6% |
| 브라우저 | Firefox 필수 | 불필요 |
| 콘텐츠 품질 | 동일 | 동일 (같은 HTML 파싱) |

불일치 잔여 3건(6%)은 YES24에 해당 판본 자체가 없는 케이스 (알라딘 리커버판, 비표준 ISBN).

---

## 설계

### 1. 워크플로우 구조

3개 독립 워크플로우로 재편:

```
daily-collect.yml     KST 03:00 (1회/일)        수집 + 보강 + Tier 1 임베딩
daily-scrape.yml      2시간마다 (12회/일)        YES24 80권씩 분산 스크래핑
daily-embed-t2.yml    KST 06:30 (1회/일)        Tier 2 임베딩
```

#### daily-collect.yml

```yaml
schedule:
  - cron: '0 18 * * *'   # UTC 18:00 = KST 03:00
workflow_dispatch: {}

steps:
  - smart_batch_collector.py --daily-target 1000    # continue-on-error
  - batch_enricher.py --limit 500                    # continue-on-error
  - tier1_embedder.py                                # continue-on-error
  - smart_batch_collector.py --status
  - batch_enricher.py --status

timeout: 30분
```

핵심: 각 스텝에 `continue-on-error: true`. 수집이 실패해도 기존 데이터에 대한 보강/임베딩은 독립 실행.

참고: `batch_enricher`는 현재 100% 완료 상태이지만, 매일 신규 수집되는 도서(~1000권)에 대해 색상/폰트를 즉시 보강하기 위해 수집 직후 실행. 신규분이 없으면 0권 처리로 수초 내 종료.

#### daily-scrape.yml

```yaml
schedule:
  - cron: '7 */2 * * *'   # 2시간마다 (매 :07분)
workflow_dispatch:
  inputs:
    limit:
      default: '80'

steps:
  - yes24_scraper.py --limit 80
  - yes24_scraper.py --status

timeout: 15분
```

핵심:
- **분산 스크래핑** — 80권 × 12회/일 = ~960권/일
- 80권 × 2.4초 = ~3분/회 (타임아웃 위험 제로)
- 2시간 간격이면 YES24 입장에서 사람 수준의 트래픽
- 백로그 8,398권 / 960권/일 = **~9일에 전량 처리**

#### daily-embed-t2.yml

```yaml
schedule:
  - cron: '30 21 * * *'   # UTC 21:30 = KST 06:30
workflow_dispatch: {}

steps:
  - tier2_embedder.py --limit 500
  - tier2_embedder.py --status

timeout: 15분
```

핵심: scrape 결과에 의존하므로 여유 있는 시간대. 매일 누적된 rich_description에 대해 Tier 2 임베딩 생성.

참고: 백로그 기간(~9일) 동안 하루 500권 limit으로 처리. 하루 ~960권 스크래핑 대비 500권이므로 점진적으로 쌓일 수 있으나, 백로그 해소 후에는 신규분만 처리하므로 자연 수렴. 급하면 limit을 올리거나 하루 2회 실행으로 대응 가능.

### 2. 스크립트 변경

#### A. `scripts/lib/retry.py` (신규)

모든 Supabase 호출을 감싸는 retry wrapper:

```python
def with_retry(fn, max_retries=3, base_delay=1.0):
    """Exponential backoff retry for Supabase calls.

    재시도 대상: 502, 503, 504, ConnectionError, TimeoutError
    즉시 실패: 4xx (400, 401, 403, 404 등)
    """
```

- 1초 → 2초 → 4초 exponential backoff + random jitter (±30%)
- 최대 3회 재시도
- 로그 출력 (재시도 횟수, 에러 타입)

적용 대상:
- `lib/state_manager.py` — `get_state()`, `upsert_state()`
- `smart_batch_collector.py` — ISBN 로드, batch upsert
- `yes24_scraper.py` — 책 조회, rich_description 저장
- `tier1_embedder.py`, `tier2_embedder.py` — 임베딩 조회/저장

#### B. `scripts/yes24_scraper.py` (전면 교체)

| 항목 | 현재 | 변경 |
|------|------|------|
| HTTP 클라이언트 | Playwright Firefox | **requests.Session + BeautifulSoup** |
| ISBN 매칭 | 첫 번째 검색 결과만 | **상위 5건 순회, ISBN 일치 시 stop** |
| 비표준 ISBN | 시도 후 실패 | **조기 스킵** (`K` prefix, 10자 미만) |
| User-Agent | Firefox (암묵적) | **브라우저 UA 명시 설정** |
| 요청 딜레이 | 2-4초 (random) | **1.0초** (고정) |
| 기본 limit | 250 | **80** (분산 실행 기준) |
| 브라우저 관리 | 시작/재시작/종료 | **삭제** |
| 의존성 | playwright | **requests, beautifulsoup4** |

검색 → ISBN 매칭 로직:

```
1. 제목+저자로 YES24 검색
2. 검색 결과의 [data-goods-no] 최대 5건 순회
3. 각 상세 페이지에서 JSON-LD의 ISBN 추출
4. DB ISBN과 비교 → 일치 시 해당 페이지에서 텍스트 추출
5. 5건 모두 불일치 → isbn_mismatch로 카운트, 스킵
```

텍스트 추출 대상 (기존과 동일):
- `#infoset_introduce` → 책소개
- `#infoset_pubReivew` → 출판사리뷰
- `#infoset_inBook` → 책속으로

#### C. `requirements.txt` 변경

```diff
+ requests
+ beautifulsoup4
```

참고: `playwright`는 `requirements.txt`가 아닌 워크플로우 YAML에서 직접 설치하고 있었으므로, `requirements.txt`에서 제거할 항목은 없음. 대신 `daily-enrich.yml`의 `playwright install firefox` 라인을 삭제.

#### D. 기존 스크립트 — 최소 변경

- `smart_batch_collector.py`: Supabase `.execute()` 호출에 retry wrapper 적용
- `batch_enricher.py`: 동일
- `tier1_embedder.py` / `tier2_embedder.py`: 동일
- `lib/state_manager.py`: 동일

로직 변경 없음. retry wrapper 감싸기만.

### 3. 정상 상태 (백로그 해소 후)

백로그 ~9일 후 rich_description이 전량 채워지면:
- `daily-scrape.yml`: 매 실행 시 대상 0-100권. 대상이 0이면 수초 내 종료 → GitHub Actions 분 소비 미미
- 12회/일 스케줄 유지해도 무방 (빈 실행 비용 < 1분/회)
- 수집이 하루 ~1000권이면 scrape도 하루 ~1000권 처리 필요 → 현재 구조로 충분

### 4. 문서 업데이트

- `docs/ARCHITECTURE.md`의 파이프라인 섹션을 새 워크플로우 구조에 맞게 업데이트
  - 2개 워크플로우 → 3개 워크플로우
  - 스케줄 및 각 워크플로우 역할 반영

### 5. 변경하지 않는 것

- DB 스키마 — 변경 없음
- 데이터 포맷 — rich_description 형식 동일 (`[책소개]\n...\n\n[출판사리뷰]\n...`)
- Tier 2 임베딩 로직 — 변경 없음
- 배치 수집 로직 (3 phase) — 변경 없음
- GitHub Actions secrets — 변경 없음

---

## 예상 효과

| 지표 | 현재 | 개선 후 |
|------|------|---------|
| 수집 안정성 | Supabase 502 → 전체 실패 | retry 3회 → 일시 장애 자동 복구 |
| YES24 처리량 | ~60권/일 (타임아웃) | **~960권/일** |
| rich_description 백로그 해소 | 34일+ | **~9일** |
| IP 차단 리스크 | 높음 (40분 집중) | 낮음 (2시간 간격 분산) |
| 워크플로우 독립성 | cancel 전파 | 각 워크플로우 독립 실행 |
| 브라우저 의존성 | Firefox 설치 필요 | 제거 |

---

## 리스크

| 리스크 | 완화 |
|--------|------|
| YES24 HTML 구조 변경 | CSS 셀렉터 기반이라 영향 범위 좁음. `--status`로 성공률 모니터링 |
| Supabase 장시간 다운 (>수분) | retry 3회로 부족. 이 경우 워크플로우 실패는 수용, 다음 실행에서 자동 복구 |
| 2시간마다 scrape가 GitHub Actions 무료 한도 소모 | 월 2000분 한도, scrape 3분×12회×30일=1080분. collect+embed 포함해도 여유 있음 |
