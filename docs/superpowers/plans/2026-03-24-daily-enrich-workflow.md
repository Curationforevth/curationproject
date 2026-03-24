# Daily Enrich Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitHub Actions 워크플로우 `daily-enrich.yml`을 생성하여 batch_enricher + yes24_scraper를 매일 자동 실행한다.

**Architecture:** 기존 `daily-batch.yml`과 별도 워크플로우로 분리. enricher → scraper 순서로 실행, enricher 실패 시에도 scraper 진행. KST 05:00 실행.

**Tech Stack:** GitHub Actions, Python 3.12, Playwright Firefox

**Spec:** `docs/superpowers/specs/2026-03-24-daily-enrich-workflow-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `.github/workflows/daily-enrich.yml` | enricher + scraper 자동화 워크플로우 |

기존 스크립트(`batch_enricher.py`, `yes24_scraper.py`)는 수정 없이 그대로 사용.

---

### Task 1: daily-enrich.yml 워크플로우 생성

**Files:**
- Create: `.github/workflows/daily-enrich.yml`
- Reference: `.github/workflows/daily-batch.yml` (기존 패턴 참고)
- Reference: `docs/superpowers/specs/2026-03-24-daily-enrich-workflow-design.md`

- [ ] **Step 1: 워크플로우 파일 생성**

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

- [ ] **Step 2: YAML 문법 검증**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-enrich.yml'))"`
Expected: 에러 없이 종료

- [ ] **Step 3: 기존 워크플로우와 비교 확인**

확인사항:
- `daily-batch.yml`과 Python/action 버전 일치 (3.12, checkout@v4, setup-python@v5)
- 시크릿 이름 일치 (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`)
- `continue-on-error: true`가 enricher 스텝에만 있는지
- `if: always()`가 status 스텝 2개에만 있는지

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/daily-enrich.yml
git commit -m "feat: daily-enrich 워크플로우 추가 — enricher + YES24 scraper 자동화"
```

---

### Task 2: 수동 실행으로 검증

- [ ] **Step 1: GitHub에 푸시**

```bash
git push origin main
```

- [ ] **Step 2: GitHub Actions에서 수동 실행**

1. GitHub 리포지토리 → Actions 탭
2. "Daily Enrichment (Color/Font + YES24 Scraping)" 워크플로우 선택
3. "Run workflow" 버튼 클릭
4. 실행 로그 확인:
   - Install dependencies: Playwright Firefox 설치 성공
   - Run batch enricher: 정상 실행 (또는 이미 전부 완료라 "모든 도서가 보강 완료됨")
   - Run YES24 scraper: 스크래핑 시작 + 건별 결과 출력
   - Show enricher/scraper status: 현황 숫자 출력
