# Daily Enrich Workflow Design

> **Deprecated (2026-03-25)**: 파이프라인 재설계로 대체됨. `2026-03-25-pipeline-redesign.md` 참조.

> GitHub Actions 워크플로우: batch_enricher + yes24_scraper 자동화

---

## 배경

현재 `daily-batch.yml`은 알라딘 수집 → Tier 1 임베딩만 처리한다. batch_enricher(색상/폰트)와 yes24_scraper(rich_description)는 수동 실행 상태로, 8,589권 중 51권만 스크래핑됨. 자동화가 필요하다.

## 결정사항

| 항목 | 결정 |
|------|------|
| 워크플로우 | 기존 daily-batch.yml과 **별도 파일** (`daily-enrich.yml`) |
| 스케줄 | UTC 20:00 (KST 05:00) — daily-batch(KST 03:00) 완료 후 2시간 버퍼 |
| 스텝 순서 | enricher → scraper (처리 스텝 2개) |
| Tier 2 임베딩 | 이 워크플로우에 포함하지 않음. 별도 P0 태스크에서 스크립트 완성 후 추가 |

### 별도 워크플로우를 선택한 이유

- 실패 격리: scraper 실패가 수집/임베딩 파이프라인에 영향 없음
- 타임아웃: daily-batch(30분)에 Playwright 스크래핑을 합치면 빡빡함
- 독립 스케줄: 수집 완료 후 시차를 두고 실행 가능

### Tier 2 임베딩을 제외한 이유

- 기존 `tier1_embedder.py`는 임베딩 없는 책만 처리 → 이미 Tier 1이 있는 책에 rich_description이 추가돼도 스킵
- 새 책 임베딩은 이미 `daily-batch.yml`에서 처리
- Tier 2 전용 스크립트(`tier2_embedder.py`)가 필요하며, 이는 별도 설계 태스크

## 워크플로우 구조

**파일**: `.github/workflows/daily-enrich.yml`

```yaml
name: Daily Enrichment (Color/Font + YES24 Scraping)

on:
  schedule:
    - cron: '0 20 * * *'  # UTC 20:00 = KST 05:00
  workflow_dispatch:  # 수동 실행

jobs:
  enrich-and-scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 45

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -r scripts/requirements.txt
          pip install playwright
          playwright install --with-deps firefox

      - name: Run batch enricher (color + font)
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/batch_enricher.py --limit 200

      - name: Run YES24 scraper (rich descriptions)
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/yes24_scraper.py --limit 250

      - name: Show enricher status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/batch_enricher.py --status

      - name: Show scraper status
        if: always()
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
        run: python scripts/yes24_scraper.py --status
```

## 스텝 상세

### Step 1: batch_enricher.py

- **대상**: `dominant_colors IS NULL` 또는 `spine_font IS NULL`인 책
- **limit**: 200권 (커버 이미지 CDN 차단 방지. 현재 99% 완료 상태라 신규 수집분만 처리하면 충분)
- **소요시간**: ~5분
- **의존성**: colorthief (requirements.txt에 포함)
- **실패 시**: `continue-on-error: true`로 scraper는 정상 실행됨

### Step 2: yes24_scraper.py --limit 250

- **대상**: `rich_description IS NULL`이고 `isbn IS NOT NULL`인 책
- **limit**: 250권/일 (현재 기본값, 45분 타임아웃 내 안전)
- **소요시간**: ~15-20분
- **의존성**: Playwright Firefox
- **메모리 관리**: 100권마다 브라우저 재시작
- **요청 간격**: 2-4초 랜덤 딜레이 (봇 차단 방지)
- **실패 시**: 부분 성공도 DB에 반영됨 (건별 upsert)

### Step 3: Status report

- enricher와 scraper 각각 `--status` 플래그로 현황 출력
- 워크플로우 로그에서 진행률 확인 가능

## 시크릿

| 시크릿 | 이미 등록? | 용도 |
|--------|-----------|------|
| `SUPABASE_URL` | Yes | DB 연결 |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | DB 쓰기 |

추가 시크릿 불필요. YES24 스크래핑은 API 키 없이 동작.

## 타임라인 (예상)

하루 250권 스크래핑 시:
- 현재 미처리: ~8,538권 (8,589 - 51)
- 완료까지: ~35일
- 84% 성공률 감안: ~42일

## 의존성 관리

- Playwright는 `requirements.txt`에 포함하지 않음 (의도적). daily-batch.yml에서 불필요한 설치를 방지하기 위해 daily-enrich.yml에서만 별도 설치.
- `--with-deps` 플래그로 Ubuntu 런너에 필요한 시스템 라이브러리도 함께 설치.

## 리스크

| 리스크 | 대응 |
|--------|------|
| YES24 봇 차단 강화 | Firefox headless 유지, 딜레이 조절. 차단 시 limit 줄여서 대응 |
| Playwright 설치 시간 (~2분) | 타임아웃 45분으로 충분한 버퍼 |
| GitHub Actions 무료 시간 소진 | 월 2,000분 중 daily-batch(~20분) + daily-enrich(~30분) = 하루 50분, 월 ~1,500분. 여유 있음 |
| 워크플로우 동시 실행 (daily-batch 지연 시) | 2시간 버퍼로 충분. 동일 행 동시 쓰기 가능성 낮고, upsert라 데이터 무결성 유지됨 |

## 향후 확장

- **Tier 2 임베딩 스크립트 완성 시**: Step 2와 Step 3(status) 사이에 `tier2_embedder.py` 스텝 추가
- **도서관 정보나루 API 승인 시**: 별도 워크플로우 또는 이 워크플로우에 스텝 추가 검토
