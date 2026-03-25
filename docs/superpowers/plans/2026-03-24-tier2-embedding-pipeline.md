# Tier 2 Embedding Pipeline Implementation Plan

> **Deprecated (2026-03-25)**: 파이프라인 재설계로 워크플로우 구조 변경됨. `2026-03-25-pipeline-redesign.md` 참조.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** rich_description 기반 점진적 임베딩 파이프라인을 구축하여, 데이터가 쌓일수록 임베딩이 풍성해지는 구조를 만든다.

**Architecture:** `compose_embedding()` 함수가 가용한 데이터 소스를 모두 활용하여 텍스트를 조합하고, `data_sources` jsonb로 어떤 소스가 사용됐는지 추적. `book_embeddings`에 upsert하여 Tier 1을 Tier 2로 업그레이드.

**Tech Stack:** Python 3.12, OpenAI text-embedding-3-small, Supabase PostgreSQL, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-24-tier2-embedding-pipeline-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `supabase/006_tier2_embedding.sql` | DB 마이그레이션 — source_text, data_sources 컬럼 추가 |
| Create | `scripts/tier2_embedder.py` | Tier 2 임베딩 생성기 메인 스크립트 |
| Create | `scripts/tests/test_tier2_embedder.py` | compose_embedding, parse_section 등 유닛 테스트 |
| Modify | `.github/workflows/daily-enrich.yml` | tier2_embedder 스텝 추가 |

---

### Task 1: DB 마이그레이션

**Files:**
- Create: `supabase/006_tier2_embedding.sql`

- [ ] **Step 1: 마이그레이션 파일 생성**

```sql
-- =============================================
-- 006: Tier 2 임베딩 파이프라인 스키마 확장
-- Spec: docs/superpowers/specs/2026-03-24-tier2-embedding-pipeline-design.md
-- =============================================

-- book_embeddings에 소스 추적 컬럼 추가
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS source_text TEXT;
ALTER TABLE public.book_embeddings ADD COLUMN IF NOT EXISTS data_sources JSONB DEFAULT '[]'::jsonb;
```

- [ ] **Step 2: Supabase SQL Editor에서 실행**

Supabase Dashboard → SQL Editor에서 위 SQL 실행.
Expected: 에러 없이 완료. `book_embeddings` 테이블에 `source_text`, `data_sources` 컬럼 추가됨.

- [ ] **Step 3: 커밋**

```bash
git add supabase/006_tier2_embedding.sql
git commit -m "chore: Tier 2 임베딩 마이그레이션 — source_text, data_sources 컬럼"
```

---

### Task 2: compose_embedding 핵심 로직 (TDD)

**Files:**
- Create: `scripts/tests/test_tier2_embedder.py`
- Create: `scripts/tier2_embedder.py`

- [ ] **Step 0: 테스트 디렉토리 생성**

Run: `mkdir -p scripts/tests`

- [ ] **Step 1: 테스트 파일 생성**

```python
"""tier2_embedder 유닛 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestParseSection:
    """rich_description에서 섹션 추출"""

    def test_parse_intro(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n이 책은 멋진 소설이다.\n\n[출판사리뷰]\n마케팅 문구"
        assert parse_section(rd, '책소개') == "이 책은 멋진 소설이다."

    def test_parse_excerpt(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n소개글\n\n[책속으로]\n나는 그날 밤을 기억한다."
        assert parse_section(rd, '책속으로') == "나는 그날 밤을 기억한다."

    def test_parse_missing_section(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n소개글\n\n[출판사리뷰]\n리뷰"
        assert parse_section(rd, '책속으로') == ""

    def test_parse_empty_rd(self):
        from tier2_embedder import parse_section
        assert parse_section("", '책소개') == ""
        assert parse_section(None, '책소개') == ""


class TestCleanIntro:
    """책소개 노이즈 제거"""

    def test_remove_stars(self):
        from tier2_embedder import clean_intro
        text = "★★★ 영화 개봉\n★ 특별판\n이 소설은 좋다."
        assert "★" not in clean_intro(text)
        assert "이 소설은 좋다." in clean_intro(text)

    def test_remove_md_comment(self):
        from tier2_embedder import clean_intro
        text = "MD 한마디\n좋은 책입니다.\n2025.06.20.\n소설 PD 김유리\n\n진짜 내용이다."
        result = clean_intro(text)
        assert "MD 한마디" not in result
        assert "좋은 책입니다." not in result
        assert "진짜 내용이다." in result

    def test_remove_preview(self):
        from tier2_embedder import clean_intro
        text = "이 소설은 좋다.\n 책의 일부 내용을 미리 읽어보실 수 있습니다. 미리보기"
        result = clean_intro(text)
        assert "미리보기" not in result
        assert "이 소설은 좋다." in result

    def test_clean_text_passthrough(self):
        from tier2_embedder import clean_intro
        text = "1980년대 서울을 배경으로 한 소설이다."
        assert clean_intro(text) == text


class TestComposeEmbedding:
    """점진적 텍스트 조합"""

    def test_aladin_only(self):
        """rich_description 없는 책은 빈 결과"""
        from tier2_embedder import compose_embedding
        book = {'title': '제목', 'author': '저자', 'genre': '소설', 'description': '설명', 'rich_description': None}
        text, sources = compose_embedding(book)
        assert text == ""
        assert sources == []

    def test_with_intro_only(self):
        """책소개만 있는 경우"""
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '알라딘 설명',
            'rich_description': '[책소개]\n멋진 소설이다.\n\n[출판사리뷰]\n마케팅',
        }
        text, sources = compose_embedding(book)
        assert '제목: 테스트책' in text
        assert '저자: 저자A' in text
        assert '장르: 한국소설' in text
        assert '내용: 알라딘 설명' in text
        assert '책소개: 멋진 소설이다.' in text
        assert 'aladin' in sources
        assert 'yes24_intro' in sources
        assert 'yes24_excerpt' not in sources

    def test_with_intro_and_excerpt(self):
        """책소개 + 책속으로"""
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '설명',
            'rich_description': '[책소개]\n멋진 소설이다.\n\n[책속으로]\n나는 그날을 기억한다.',
        }
        text, sources = compose_embedding(book)
        assert '책소개: 멋진 소설이다.' in text
        assert '발췌: 나는 그날을 기억한다.' in text
        assert 'yes24_intro' in sources
        assert 'yes24_excerpt' in sources

    def test_excerpt_truncated_to_300(self):
        """책속으로는 300자로 잘림"""
        from tier2_embedder import compose_embedding
        long_excerpt = "가" * 500
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': f'[책소개]\n소개\n\n[책속으로]\n{long_excerpt}',
        }
        text, _ = compose_embedding(book)
        # 발췌: 다음에 300자만 있어야 함
        excerpt_part = text.split('발췌: ')[1]
        assert len(excerpt_part) == 300

    def test_no_intro_returns_empty(self):
        """책소개 파싱 실패 시 빈 결과 (Tier 2 최소 조건 미달)"""
        from tier2_embedder import compose_embedding
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': '[출판사리뷰]\n마케팅만 있음',
        }
        text, sources = compose_embedding(book)
        assert text == ""
        assert sources == []

    def test_truncate_long_text(self):
        """15000자(~7500토큰) 초과 시 잘림"""
        from tier2_embedder import compose_embedding
        long_intro = "가" * 20000
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': f'[책소개]\n{long_intro}',
        }
        text, _ = compose_embedding(book)
        assert len(text) <= 15000
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd scripts && python -m pytest tests/test_tier2_embedder.py -v`
Expected: ImportError — tier2_embedder 모듈 없음

- [ ] **Step 3: tier2_embedder.py 핵심 함수 구현**

```python
"""
Tier 2 임베딩 생성기 — 점진적 임베딩 파이프라인

rich_description이 있는 도서의 임베딩을 업그레이드.
데이터 소스가 추가될 때마다 임베딩이 풍성해지는 구조.

사용법:
  python3 scripts/tier2_embedder.py                      # 미처리분
  python3 scripts/tier2_embedder.py --limit 300           # 최대 300권
  python3 scripts/tier2_embedder.py --force --limit 500   # 강제 재생성
  python3 scripts/tier2_embedder.py --dry-run             # DB 저장 없이 테스트
  python3 scripts/tier2_embedder.py --status              # 현황 조회
"""

import argparse
import json
import os
import re
import sys
import time

try:
    from dotenv import load_dotenv
    from openai import OpenAI
    from supabase import create_client
    load_dotenv()
except ImportError:
    pass  # 테스트 환경에서는 순수 함수만 사용

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50
MAX_CHARS = 15000  # ~7500 토큰 (한국어 ~2자 = ~1토큰)
EXCERPT_LIMIT = 300


def parse_section(rich_description, section_name):
    """rich_description에서 특정 섹션 텍스트 추출.

    포맷: [책소개]\\n텍스트\\n\\n[출판사리뷰]\\n텍스트\\n\\n[책속으로]\\n텍스트
    """
    if not rich_description:
        return ""

    marker = f"[{section_name}]"
    if marker not in rich_description:
        return ""

    # 마커 이후 텍스트 추출
    after = rich_description.split(marker, 1)[1]

    # 다음 섹션 마커 전까지
    next_markers = ['[책소개]', '[출판사리뷰]', '[책속으로]']
    end = len(after)
    for m in next_markers:
        if m != marker and m in after:
            idx = after.index(m)
            if idx < end:
                end = idx

    return after[:end].strip()


def clean_intro(text):
    """책소개 텍스트에서 노이즈 라인 제거."""
    if not text:
        return ""

    lines = text.split('\n')
    cleaned = []
    skip_md = False

    for line in lines:
        stripped = line.strip()

        # ★ 로 시작하는 마케팅 라인
        if stripped.startswith('★'):
            continue

        # MD 한마디 블록 (MD 한마디 ~ 다음 빈 줄까지)
        if stripped.startswith('MD 한마디') or stripped.startswith('MD한마디'):
            skip_md = True
            continue
        if skip_md:
            if stripped == '':
                skip_md = False
            continue

        # 미리보기 라인
        if '미리보기' in stripped:
            continue

        cleaned.append(line)

    return '\n'.join(cleaned).strip()


def compose_embedding(book):
    """가용한 데이터를 모두 활용하여 임베딩 텍스트 조합.

    Returns:
        (text, data_sources): 텍스트와 사용된 소스 목록.
        rich_description이 없거나 책소개 파싱 실패 시 ("", []) 반환.
    """
    rd = book.get('rich_description')
    if not rd:
        return "", []

    # 책소개 필수 (Tier 2 최소 조건)
    intro = clean_intro(parse_section(rd, '책소개'))
    if not intro:
        return "", []

    parts = []
    data_sources = ['aladin']

    # 기본 메타데이터 (항상)
    if book.get('title'):
        parts.append(f"제목: {book['title']}")
    if book.get('author'):
        parts.append(f"저자: {book['author']}")
    if book.get('genre'):
        parts.append(f"장르: {book['genre']}")
    if book.get('description'):
        parts.append(f"내용: {book['description']}")

    # YES24 책소개
    parts.append(f"책소개: {intro}")
    data_sources.append('yes24_intro')

    # YES24 책속으로 발췌
    excerpt = parse_section(rd, '책속으로')
    if excerpt:
        parts.append(f"발췌: {excerpt[:EXCERPT_LIMIT]}")
        data_sources.append('yes24_excerpt')

    # (미래) 도서관 키워드
    # if book.get('library_keywords'):
    #     parts.append(f"키워드: {', '.join(book['library_keywords'])}")
    #     data_sources.append('library_keywords')

    text = '\n'.join(parts)

    # 토큰 안전장치
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    return text, data_sources


class Tier2Embedder:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.stats = {"processed": 0, "embedded": 0, "skipped": 0, "errors": 0}

    def fetch_books_needing_tier2(self, limit=0, force=False):
        """rich_description이 있고, Tier 2 임베딩이 아직 없는 책 조회."""
        # rich_description이 있는 책 조회 (페이징)
        all_books = []
        offset = 0
        page_size = 1000
        while True:
            result = self.sb.table("books") \
                .select("id, title, author, genre, description, rich_description") \
                .not_.is_("rich_description", "null") \
                .range(offset, offset + page_size - 1) \
                .execute()
            if not result.data:
                break
            all_books.extend(result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

        if force:
            books = all_books
        else:
            # data_sources에 yes24_intro가 없는 것만 필터
            existing = {}
            offset = 0
            while True:
                result = self.sb.table("book_embeddings") \
                    .select("book_id, data_sources") \
                    .range(offset, offset + page_size - 1) \
                    .execute()
                if not result.data:
                    break
                for row in result.data:
                    existing[row["book_id"]] = row.get("data_sources") or []
                if len(result.data) < page_size:
                    break
                offset += page_size

            books = [
                b for b in all_books
                if b["id"] not in existing
                or "yes24_intro" not in (existing.get(b["id"]) or [])
            ]

        if limit > 0:
            books = books[:limit]

        return books

    def run(self, limit=0, force=False):
        """메인 실행."""
        print(f"🔍 Tier 2 대상 도서 조회 중... {'(force)' if force else ''}")
        books = self.fetch_books_needing_tier2(limit=limit, force=force)
        print(f"   {len(books)}권 발견\n")

        if not books:
            print("✅ Tier 2 임베딩이 필요한 도서가 없습니다.")
            return

        # compose + filter
        valid_books = []
        for book in books:
            text, sources = compose_embedding(book)
            if text:
                valid_books.append((book, text, sources))
            else:
                self.stats["skipped"] += 1

        if not valid_books:
            print(f"⚠ 유효한 텍스트를 생성할 수 없는 도서 {self.stats['skipped']}권 스킵.")
            return

        print(f"  {len(valid_books)}권 처리 예정 ({self.stats['skipped']}권 스킵)\n")

        # 배치 임베딩
        for i in range(0, len(valid_books), BATCH_SIZE):
            batch = valid_books[i : i + BATCH_SIZE]
            texts = [t for _, t, _ in batch]
            book_ids = [b["id"] for b, _, _ in batch]
            sources_list = [s for _, _, s in batch]

            try:
                response = self.openai_client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                )
                embeddings = [item.embedding for item in response.data]

                if not self.dry_run:
                    rows = [
                        {
                            "book_id": bid,
                            "embedding": emb,
                            "tier": 2,
                            "source_text": txt,
                            "data_sources": src,
                        }
                        for bid, emb, txt, src in zip(book_ids, embeddings, texts, sources_list)
                    ]
                    self.sb.table("book_embeddings").upsert(
                        rows, on_conflict="book_id"
                    ).execute()

                self.stats["embedded"] += len(batch)
                prefix = "(dry-run) " if self.dry_run else ""
                print(f"  {prefix}배치 {i // BATCH_SIZE + 1}: {len(batch)}권 Tier 2 임베딩 완료")

            except Exception as e:
                self.stats["errors"] += 1
                print(f"  ✗ 배치 {i // BATCH_SIZE + 1} 실패: {e}")

            time.sleep(0.5)

        self.print_report()

    def print_report(self):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}Tier 2 임베딩 결과")
        print(f"{'=' * 50}")
        print(f"  임베딩 완료: {s['embedded']}권")
        print(f"  스킵 (책소개 없음): {s['skipped']}권")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total = self.sb.table("books").select("id", count="exact").execute()
        has_rich = self.sb.table("books").select("id", count="exact") \
            .not_.is_("rich_description", "null").execute()

        tier1 = self.sb.table("book_embeddings").select("id", count="exact") \
            .eq("tier", 1).execute()
        tier2 = self.sb.table("book_embeddings").select("id", count="exact") \
            .eq("tier", 2).execute()

        print(f"\n{'=' * 50}")
        print("Tier 2 임베딩 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total.count}권")
        print(f"  rich_description 수집 완료: {has_rich.count}권")
        print(f"  Tier 1 임베딩: {tier1.count}권")
        print(f"  Tier 2 임베딩: {tier2.count}권")
        print(f"  Tier 2 대기: {has_rich.count - tier2.count}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="Tier 2 임베딩 생성기")
    parser.add_argument("--limit", type=int, default=0, help="최대 처리 권수 (0=전부)")
    parser.add_argument("--force", action="store_true", help="강제 재생성 (--limit 필수)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="현황 조회")
    args = parser.parse_args()

    if args.force and args.limit == 0:
        print("❌ --force 사용 시 --limit을 반드시 지정해주세요.")
        sys.exit(1)

    embedder = Tier2Embedder(dry_run=args.dry_run)

    if args.status:
        embedder.show_status()
        return

    embedder.run(limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd scripts && python -m pytest tests/test_tier2_embedder.py -v`
Expected: 11 tests passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/tier2_embedder.py scripts/tests/test_tier2_embedder.py
git commit -m "feat: Tier 2 임베딩 생성기 — 점진적 임베딩 파이프라인"
```

---

### Task 3: 로컬 테스트 (dry-run)

**Files:**
- Reference: `scripts/tier2_embedder.py`

- [ ] **Step 1: dry-run으로 동작 확인**

Run: `cd scripts && python tier2_embedder.py --dry-run --limit 5`
Expected:
```
🔍 Tier 2 대상 도서 조회 중...
   N권 발견

  N권 처리 예정 (M권 스킵)

  (dry-run) 배치 1: N권 Tier 2 임베딩 완료

(dry-run) Tier 2 임베딩 결과
==================================================
  임베딩 완료: N권
  스킵 (책소개 없음): M권
  에러: 0건
```

- [ ] **Step 2: status 확인**

Run: `cd scripts && python tier2_embedder.py --status`
Expected: Tier 2 현황 테이블 출력

- [ ] **Step 3: 실제 실행 (49권)**

Run: `cd scripts && python tier2_embedder.py`
Expected: 49권 중 유효한 책이 Tier 2로 업그레이드됨

- [ ] **Step 4: 커밋**

```bash
git commit --allow-empty -m "test: Tier 2 embedder 로컬 실행 검증 완료 (49권)"
```

---

### Task 4: daily-enrich.yml 업데이트

**Files:**
- Modify: `.github/workflows/daily-enrich.yml`

- [ ] **Step 1: scraper와 status 사이에 tier2_embedder 스텝 추가**

yes24_scraper 스텝 뒤, Show enricher status 스텝 앞에 추가:

```yaml
      - name: Run Tier 2 embedder
        continue-on-error: true
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python scripts/tier2_embedder.py --limit 300
```

- [ ] **Step 2: YAML 검증**

```python
python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-enrich.yml'))"
```

또는 기존 검증 방식:
```python
python -c "
with open('.github/workflows/daily-enrich.yml') as f:
    c = f.read()
checks = {
    'tier2 step exists': 'tier2_embedder.py' in c,
    'OPENAI_API_KEY': 'OPENAI_API_KEY' in c,
    'continue-on-error on embedder': c.count('continue-on-error: true') >= 2,
}
for k, v in checks.items():
    print(f\"{'PASS' if v else 'FAIL'}: {k}\")
"
```

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/daily-enrich.yml
git commit -m "feat: daily-enrich에 Tier 2 embedder 스텝 추가"
```

---

### Task 5: 푸시 및 검증

- [ ] **Step 1: 전체 테스트 재실행**

Run: `cd scripts && python -m pytest tests/test_tier2_embedder.py -v`
Expected: 11 tests passed

- [ ] **Step 2: git push**

```bash
git push origin main
```

- [ ] **Step 3: GitHub Actions 수동 실행으로 검증**

GitHub → Actions → "Daily Enrichment" → Run workflow
확인사항:
- Install dependencies: 정상
- Run batch enricher: 정상 또는 "모든 도서가 보강 완료됨"
- Run YES24 scraper: 스크래핑 진행
- **Run Tier 2 embedder**: 새로 스크래핑된 책 + 기존 미처리분 임베딩
- Show enricher/scraper status: 현황 출력
