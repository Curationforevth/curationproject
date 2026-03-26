# 추천 엔진 v2 구현 플랜 — "좋아할 이유" 기반 매칭

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 임베딩 유사도 기반 추천을 "좋아할 이유" 텍스트 매칭 기반으로 전환하여, 유저 피드백이 추천 결과를 직접 결정하는 구조 구축.

**Architecture:** 책마다 "좋아할 이유" 텍스트를 LLM으로 추출하고 text-embedding-3-large로 임베딩하여 저장. 유저 피드백에서도 "좋아하는 이유"를 추출. 추천 시 유저 이유 임베딩과 책 이유 임베딩의 코사인 유사도로 후보를 생성하고, 품질 기반(AVG+MAX) 스코어링으로 랭킹.

**Tech Stack:** Python 3.12, Supabase (PostgreSQL + pgvector), OpenAI API (gpt-4o-mini, text-embedding-3-large), GitHub Actions, pytest

**Spec:** `docs/superpowers/specs/2026-03-26-recommendation-engine-v2-design.md`

---

## 파일 구조

| 경로 | 역할 |
|------|------|
| `supabase/010_love_reasons.sql` | 신규 테이블 + RPC 마이그레이션 |
| `scripts/reason_extractor.py` | 책 "좋아할 이유" 추출 + 임베딩 파이프라인 |
| `scripts/lib/openai_helpers.py` | OpenAI API 직접 호출 헬퍼 (패키지 호환 문제 우회) |
| `tests/test_reason_extractor.py` | reason_extractor 유닛 테스트 |
| `.github/workflows/daily-extract-reasons.yml` | 일일 이유 추출 배치 워크플로우 |

---

### Task 1: OpenAI 헬퍼 모듈

기존 openai 패키지가 호환 문제가 있어 requests로 직접 호출하는 패턴이 실험 스크립트에서 검증됨. 이를 재사용 가능한 모듈로 분리.

**Files:**
- Create: `scripts/lib/openai_helpers.py`
- Create: `tests/test_openai_helpers.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_openai_helpers.py
"""OpenAI 헬퍼 유닛 테스트 — API 호출 없이 순수 함수만"""
import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from lib.openai_helpers import build_chat_payload, build_embedding_payload, parse_chat_response, parse_embedding_response


def test_build_chat_payload():
    payload = build_chat_payload("테스트 프롬프트", temperature=0.3)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][0]["content"] == "테스트 프롬프트"
    assert payload["temperature"] == 0.3
    assert payload["response_format"]["type"] == "json_object"


def test_build_embedding_payload_single():
    payload = build_embedding_payload(["hello"])
    assert payload["model"] == "text-embedding-3-large"
    assert payload["input"] == ["hello"]


def test_build_embedding_payload_batch():
    texts = [f"text_{i}" for i in range(5)]
    payload = build_embedding_payload(texts)
    assert len(payload["input"]) == 5


def test_parse_chat_response():
    mock_response = {
        "choices": [{"message": {"content": '{"reasons": ["이유1", "이유2"]}'}}]
    }
    result = parse_chat_response(mock_response)
    assert result == {"reasons": ["이유1", "이유2"]}


def test_parse_embedding_response():
    mock_response = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
        ]
    }
    result = parse_embedding_response(mock_response)
    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd scripts && python -m pytest ../tests/test_openai_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.openai_helpers'`

- [ ] **Step 3: 구현**

```python
# scripts/lib/openai_helpers.py
"""OpenAI API 직접 호출 헬퍼.

openai 패키지 호환 문제(jiter 모듈) 우회를 위해 requests로 직접 호출.
실험 스크립트(experiment_attributes.py)에서 검증된 패턴.
"""

import json
import os

import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072
API_TIMEOUT = 60


def build_chat_payload(prompt, temperature=0.3):
    """LLM 채팅 요청 페이로드 구성."""
    return {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }


def build_embedding_payload(texts):
    """임베딩 요청 페이로드 구성."""
    return {
        "model": EMBEDDING_MODEL,
        "input": texts,
    }


def parse_chat_response(response_json):
    """LLM 응답에서 JSON 파싱."""
    content = response_json["choices"][0]["message"]["content"]
    return json.loads(content)


def parse_embedding_response(response_json):
    """임베딩 응답에서 벡터 리스트 추출."""
    return [d["embedding"] for d in response_json["data"]]


def call_chat(prompt, temperature=0.3):
    """LLM 호출 (JSON 응답 반환)."""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=build_chat_payload(prompt, temperature),
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_chat_response(resp.json())


def call_embedding(texts):
    """임베딩 호출 (벡터 리스트 반환)."""
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=build_embedding_payload(texts),
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_embedding_response(resp.json())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_openai_helpers.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/lib/openai_helpers.py tests/test_openai_helpers.py
git commit -m "feat: OpenAI API 직접 호출 헬퍼 모듈 + 테스트"
```

---

### Task 2: DB 마이그레이션 — 테이블 + RPC

**Files:**
- Create: `supabase/010_love_reasons.sql`

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- supabase/010_love_reasons.sql
-- =============================================
-- 010: 추천 엔진 v2 — "좋아할 이유" 기반 매칭
-- Spec: docs/superpowers/specs/2026-03-26-recommendation-engine-v2-design.md
-- =============================================

-- 1. 책의 "좋아할 이유"
CREATE TABLE IF NOT EXISTS public.book_love_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id UUID REFERENCES public.books(id) NOT NULL,
  reason TEXT NOT NULL,
  reason_embedding VECTOR(3072),
  source TEXT NOT NULL DEFAULT 'llm_extracted',
  user_mention_count INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_blr_book ON public.book_love_reasons(book_id);
CREATE INDEX IF NOT EXISTS idx_blr_embedding
  ON public.book_love_reasons USING ivfflat (reason_embedding vector_cosine_ops)
  WITH (lists = 100);

-- 2. 유저의 "좋아하는 이유"
CREATE TABLE IF NOT EXISTS public.user_taste_reasons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  book_id UUID REFERENCES public.books(id) NOT NULL,
  reason TEXT NOT NULL,
  reason_embedding VECTOR(3072),
  weight FLOAT NOT NULL DEFAULT 1.0,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_utr_user ON public.user_taste_reasons(user_id);
CREATE INDEX IF NOT EXISTS idx_utr_embedding
  ON public.user_taste_reasons USING ivfflat (reason_embedding vector_cosine_ops)
  WITH (lists = 100);

-- 3. RPC: 이유 기반 추천
CREATE OR REPLACE FUNCTION public.recommend_books_by_reasons(
  p_user_id UUID,
  p_match_count INT DEFAULT 20
)
RETURNS TABLE (book_id UUID, title TEXT, score FLOAT, matched_reason TEXT)
AS $$
  WITH user_reasons AS (
    SELECT id AS reason_id, reason_embedding, weight
    FROM public.user_taste_reasons
    WHERE user_id = p_user_id AND weight > 0
  ),
  raw_matches AS (
    SELECT
      ur.reason_id,
      ur.weight,
      blr.book_id,
      1 - (blr.reason_embedding <=> ur.reason_embedding) AS similarity,
      blr.reason AS matched_reason
    FROM user_reasons ur
    CROSS JOIN LATERAL (
      SELECT book_id, reason_embedding, reason
      FROM public.book_love_reasons
      ORDER BY reason_embedding <=> ur.reason_embedding
      LIMIT 100
    ) blr
  ),
  best_per_pair AS (
    SELECT DISTINCT ON (reason_id, book_id)
      reason_id, book_id, weight, similarity, matched_reason
    FROM raw_matches
    ORDER BY reason_id, book_id, similarity DESC
  ),
  book_scores AS (
    SELECT
      book_id,
      AVG(weight * similarity) AS avg_score,
      MAX(weight * similarity) AS best_score,
      (ARRAY_AGG(matched_reason ORDER BY weight * similarity DESC))[1] AS top_reason
    FROM best_per_pair
    WHERE book_id NOT IN (
      SELECT fb.book_id FROM public.user_book_feedback fb WHERE fb.user_id = p_user_id
    )
    AND book_id NOT IN (
      SELECT b.id FROM public.books b WHERE b.canonical_book_id IS NOT NULL
    )
    GROUP BY book_id
  )
  SELECT bs.book_id, b.title,
    (bs.avg_score * 0.7 + bs.best_score * 0.3)::FLOAT AS score,
    bs.top_reason AS matched_reason
  FROM book_scores bs
  JOIN public.books b ON b.id = bs.book_id
  ORDER BY score DESC
  LIMIT p_match_count;
$$ LANGUAGE sql STABLE;

-- 4. RLS
ALTER TABLE public.book_love_reasons ENABLE ROW LEVEL SECURITY;
CREATE POLICY "book_love_reasons_read" ON public.book_love_reasons
  FOR SELECT USING (true);
CREATE POLICY "book_love_reasons_service" ON public.book_love_reasons
  FOR ALL USING (auth.role() = 'service_role');

ALTER TABLE public.user_taste_reasons ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user_taste_reasons_own" ON public.user_taste_reasons
  FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_taste_reasons_service" ON public.user_taste_reasons
  FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 2: Supabase SQL Editor에서 실행**

Supabase 대시보드 > SQL Editor에 위 SQL을 붙여넣고 실행. `Success. No rows returned` 확인.

- [ ] **Step 3: 테이블 생성 확인**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation" && python3 -c "
import os
from dotenv import load_dotenv
from supabase import create_client
load_dotenv('.env')
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
r1 = sb.table('book_love_reasons').select('id').limit(1).execute()
r2 = sb.table('user_taste_reasons').select('id').limit(1).execute()
print(f'book_love_reasons: OK ({len(r1.data)} rows)')
print(f'user_taste_reasons: OK ({len(r2.data)} rows)')
"
```

Expected: 두 테이블 모두 OK (0 rows)

- [ ] **Step 4: 커밋**

```bash
git add supabase/010_love_reasons.sql
git commit -m "feat: 추천 v2 마이그레이션 — book_love_reasons, user_taste_reasons, RPC"
```

---

### Task 3: Reason Extractor — 순수 함수 + 테스트

책의 "좋아할 이유"를 추출하는 핵심 로직. API 호출 없이 테스트 가능한 순수 함수와 파이프라인 클래스를 분리.

**Files:**
- Create: `scripts/reason_extractor.py`
- Create: `tests/test_reason_extractor.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_reason_extractor.py
"""Reason Extractor 유닛 테스트 — 순수 함수만"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from reason_extractor import build_extraction_prompt, parse_reasons, build_feedback_prompt, filter_generic_reasons


def test_build_extraction_prompt_includes_title():
    prompt = build_extraction_prompt(
        title="해리 포터와 마법사의 돌",
        genre="소설/시/희곡>판타지",
        description="마법 학교 이야기",
        library_keywords=["해리포터", "마법"],
    )
    assert "해리 포터와 마법사의 돌" in prompt
    assert "판타지" in prompt
    assert "마법 학교 이야기" in prompt


def test_build_extraction_prompt_handles_empty_fields():
    prompt = build_extraction_prompt(
        title="테스트 책",
        genre="",
        description="",
        library_keywords=None,
    )
    assert "테스트 책" in prompt


def test_parse_reasons_valid():
    raw = {"reasons": ["이유 하나", "이유 둘", "이유 셋"]}
    result = parse_reasons(raw)
    assert result == ["이유 하나", "이유 둘", "이유 셋"]


def test_parse_reasons_filters_empty():
    raw = {"reasons": ["이유 하나", "", "  ", "이유 둘"]}
    result = parse_reasons(raw)
    assert result == ["이유 하나", "이유 둘"]


def test_parse_reasons_invalid_format():
    raw = {"error": "something"}
    result = parse_reasons(raw)
    assert result == []


def test_filter_generic_reasons():
    reasons = [
        "호그와트의 디테일한 마법 학교 생활",  # 구체적 → 유지
        "재밌다",  # 범용 → 제거
        "감동적이다",  # 범용 → 제거
        "마법사의 돌을 둘러싼 미스터리와 반전",  # 구체적 → 유지
        "좋은 책",  # 범용 → 제거
        "읽어볼 만하다",  # 범용 → 제거
    ]
    filtered = filter_generic_reasons(reasons)
    assert "호그와트의 디테일한 마법 학교 생활" in filtered
    assert "마법사의 돌을 둘러싼 미스터리와 반전" in filtered
    assert "재밌다" not in filtered
    assert "좋은 책" not in filtered


def test_build_feedback_prompt():
    prompt = build_feedback_prompt("세계관이 새롭고 디테일하고 몰입이 되었어")
    assert "세계관이 새롭고" in prompt


def test_build_feedback_prompt_short_input():
    prompt = build_feedback_prompt("좋았어요")
    assert "좋았어요" in prompt
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd scripts && python -m pytest ../tests/test_reason_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

```python
# scripts/reason_extractor.py
"""책의 "좋아할 이유" 추출 + 임베딩 파이프라인.

배치: 이유가 없는 책에 대해 LLM 추출 + embedding-3-large 임베딩.
수집 연동: 새 책 등록 시 즉시 호출 가능.

사용법:
  python3 scripts/reason_extractor.py                  # 미처리분 전체
  python3 scripts/reason_extractor.py --limit 100      # 최대 100권
  python3 scripts/reason_extractor.py --dry-run        # DB 저장 없이
  python3 scripts/reason_extractor.py --status         # 현황
"""

import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

try:
    from lib.openai_helpers import call_chat, call_embedding
except ImportError:
    pass

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()

BATCH_SIZE = 20  # LLM 호출은 개별, 임베딩은 배치
MIN_REASON_LENGTH = 4  # 최소 글자수

# 범용 표현 필터 — 어떤 책에든 적용 가능한 모호한 표현
GENERIC_PATTERNS = [
    r'^재밌다$', r'^재미있다$', r'^감동적이다$', r'^좋은 책$',
    r'^좋다$', r'^읽어볼 만하다$', r'^추천한다$', r'^괜찮다$',
    r'^잘 읽힌다$', r'^흥미롭다$', r'^인상적이다$',
]
GENERIC_RE = re.compile('|'.join(GENERIC_PATTERNS))


def build_extraction_prompt(title, genre, description, library_keywords=None):
    """책의 '좋아할 이유' 추출 프롬프트 구성."""
    parts = [f"작품: {title}"]
    if genre:
        parts.append(f"장르: {genre}")
    if description:
        parts.append(f"설명: {description[:500]}")
    if library_keywords:
        kw_str = ", ".join(library_keywords[:20]) if isinstance(library_keywords, list) else str(library_keywords)[:200]
        parts.append(f"키워드: {kw_str}")
    book_info = "\n".join(parts)

    return f"""이 작품을 읽은 독자가 좋아할 만한 이유를 추출해주세요.

규칙:
- 특정 판본/에디션이 아닌 **작품 자체**의 매력을 써주세요
- 설명에 없더라도 이 작품에 대해 알고 있는 내용을 활용하세요
- 각 이유는 **10~30단어의 구체적인 한 문장**으로
- "이 책의~" 같은 서두 없이 핵심만
- 이 작품만의 구체적 특징을 담아야 함 ("재밌다", "감동적이다" 같은 범용 표현 제외)
- 유의미한 이유만 (3~8개)

{book_info}

JSON: {{"reasons": ["이유1", "이유2", ...]}}"""


def build_feedback_prompt(feedback_text):
    """유저 피드백에서 '좋아하는 이유' 추출 프롬프트 구성."""
    return f"""유저가 책에 대해 남긴 피드백에서, 이 사람이 책을 좋아하는 이유를 추출해주세요.

규칙:
- 피드백에서 직접 언급하거나 암시하는 것만
- 각 이유는 2~6단어의 짧은 구(phrase)로
- 없는 말 만들지 마세요
- 피드백이 너무 모호하면 빈 리스트 반환

피드백: "{feedback_text}"

JSON: {{"reasons": ["이유1", "이유2", ...]}}"""


def parse_reasons(raw_response):
    """LLM 응답에서 이유 리스트 추출 + 정리."""
    reasons = raw_response.get("reasons", [])
    if not isinstance(reasons, list):
        return []
    return [r.strip() for r in reasons if isinstance(r, str) and r.strip()]


def filter_generic_reasons(reasons):
    """범용 표현 제거. 구체적 이유만 남김."""
    filtered = []
    for r in reasons:
        if len(r) < MIN_REASON_LENGTH:
            continue
        if GENERIC_RE.match(r):
            continue
        filtered.append(r)
    return filtered


class ReasonExtractor:
    """책의 좋아할 이유 추출 + 임베딩 파이프라인."""

    def __init__(self, sb, dry_run=False):
        self.sb = sb
        self.dry_run = dry_run
        self.stats = {"processed": 0, "reasons_created": 0, "skipped": 0, "errors": 0}

    def fetch_books_without_reasons(self, limit=None):
        """book_love_reasons가 없는 책 조회."""
        query = self.sb.table("books").select(
            "id, title, genre, description, rich_description, library_keywords"
        ).not_.in_(
            "id",
            self.sb.table("book_love_reasons").select("book_id").execute().data
            if not limit else []  # 작은 배치면 서브쿼리 스킵
        ).order("sales_point", desc=True, nulls_last=True)

        if limit:
            query = query.limit(limit)
        return query.execute().data

    def extract_and_save(self, book):
        """단일 책의 이유 추출 + 임베딩 + 저장."""
        desc = book.get("rich_description") or book.get("description") or ""
        desc = re.sub(r"<[^>]+>", "", desc)  # HTML 태그 제거

        prompt = build_extraction_prompt(
            title=book["title"],
            genre=book.get("genre", ""),
            description=desc,
            library_keywords=book.get("library_keywords"),
        )

        try:
            raw = call_chat(prompt)
            reasons = filter_generic_reasons(parse_reasons(raw))
        except Exception as e:
            print(f"  LLM 오류: {book['title'][:30]} — {e}")
            self.stats["errors"] += 1
            return []

        if not reasons:
            self.stats["skipped"] += 1
            return []

        # 임베딩
        try:
            embeddings = call_embedding(reasons)
        except Exception as e:
            print(f"  임베딩 오류: {book['title'][:30]} — {e}")
            self.stats["errors"] += 1
            return []

        # 저장
        rows = []
        for reason, emb in zip(reasons, embeddings):
            rows.append({
                "book_id": book["id"],
                "reason": reason,
                "reason_embedding": emb,
                "source": "llm_extracted",
            })

        if not self.dry_run and rows:
            try:
                with_retry(lambda: self.sb.table("book_love_reasons").insert(rows).execute())
            except Exception as e:
                print(f"  DB 저장 오류: {book['title'][:30]} — {e}")
                self.stats["errors"] += 1
                return []

        self.stats["processed"] += 1
        self.stats["reasons_created"] += len(rows)
        return reasons

    def run(self, limit=None):
        """배치 실행."""
        # 이미 이유가 있는 book_id 조회
        existing = self.sb.table("book_love_reasons").select("book_id").execute().data
        existing_ids = {r["book_id"] for r in existing}

        # 이유 없는 책 조회
        query = self.sb.table("books").select(
            "id, title, genre, description, rich_description, library_keywords"
        ).order("sales_point", desc=True, nulls_last=True)
        if limit:
            query = query.limit(limit + len(existing_ids))  # 여유분
        books = query.execute().data

        # 기존 이유 있는 책 제외
        books = [b for b in books if b["id"] not in existing_ids]
        if limit:
            books = books[:limit]

        print(f"처리 대상: {len(books)}권 (기존 이유: {len(existing_ids)}권)")

        for i, book in enumerate(books):
            reasons = self.extract_and_save(book)
            if reasons:
                print(f"  [{i+1}/{len(books)}] {book['title'][:30]} → {len(reasons)}개 이유")
            time.sleep(0.3)  # rate limit

        return self.stats

    def print_report(self):
        """실행 결과 출력."""
        s = self.stats
        print(f"\n{'=' * 40}")
        print(f"처리: {s['processed']}권")
        print(f"생성된 이유: {s['reasons_created']}개")
        print(f"건너뜀 (이유 없음): {s['skipped']}권")
        print(f"오류: {s['errors']}건")
        print(f"{'=' * 40}")

    @staticmethod
    def get_status(sb):
        """현황 조회."""
        total_books = sb.table("books").select("id", count="exact").execute().count
        books_with_reasons = sb.rpc("", {})  # 아래 쿼리로 대체
        reasons = sb.table("book_love_reasons").select("id", count="exact").execute().count
        books_covered = len({r["book_id"] for r in
            sb.table("book_love_reasons").select("book_id").execute().data})

        print(f"전체 도서: {total_books}권")
        print(f"이유 있는 도서: {books_covered}권 ({books_covered/total_books*100:.1f}%)")
        print(f"총 이유 수: {reasons}개 (평균 {reasons/max(books_covered,1):.1f}개/권)")


def main():
    parser = argparse.ArgumentParser(description="책 '좋아할 이유' 추출 파이프라인")
    parser.add_argument("--limit", type=int, help="최대 처리 권수")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="현황 조회")
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    if args.status:
        ReasonExtractor.get_status(sb)
        return

    mode = "미리보기" if args.dry_run else "실행"
    print(f"Reason Extractor — {mode} 모드")

    extractor = ReasonExtractor(sb, dry_run=args.dry_run)
    extractor.run(limit=args.limit)
    extractor.print_report()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd scripts && python -m pytest ../tests/test_reason_extractor.py -v`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add scripts/reason_extractor.py tests/test_reason_extractor.py
git commit -m "feat: Reason Extractor — 좋아할 이유 추출 파이프라인 + 테스트"
```

---

### Task 4: 초기 배치 실행 — 8,500권 이유 추출

**Files:**
- Modify: `scripts/reason_extractor.py` (실행만)

- [ ] **Step 1: dry-run으로 10권 테스트**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
python3 scripts/reason_extractor.py --dry-run --limit 10
```

Expected: 10권 처리, 이유 추출 결과 출력, DB 변경 없음.

- [ ] **Step 2: 실제 10권 저장 테스트**

```bash
python3 scripts/reason_extractor.py --limit 10
```

Expected: DB에 ~60~80개 reason 저장됨. `--status`로 확인.

- [ ] **Step 3: DB 확인**

```bash
python3 scripts/reason_extractor.py --status
```

Expected: `이유 있는 도서: 10권`, `총 이유 수: ~60개`

- [ ] **Step 4: 전체 배치 실행**

```bash
python3 scripts/reason_extractor.py --limit 500
```

500권씩 나눠서 실행. 중간에 중단해도 재실행 시 이미 처리된 책은 건너뜀.
전체 8,500권은 ~17회 실행 또는 `--limit` 없이 한 번에.

- [ ] **Step 5: 최종 현황 확인**

```bash
python3 scripts/reason_extractor.py --status
```

Expected: 이유 있는 도서 비율 90%+ (rich_description 없는 책은 품질 낮아 건너뛸 수 있음)

---

### Task 5: GitHub Actions 워크플로우

**Files:**
- Create: `.github/workflows/daily-extract-reasons.yml`

- [ ] **Step 1: 워크플로우 작성**

```yaml
# .github/workflows/daily-extract-reasons.yml
name: daily-extract-reasons

on:
  schedule:
    - cron: '0 20 * * *'  # UTC 20:00 = KST 05:00
  workflow_dispatch:

jobs:
  extract-reasons:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Extract reasons for new books
        run: python scripts/reason_extractor.py --limit 100

      - name: Show status
        run: python scripts/reason_extractor.py --status
```

- [ ] **Step 2: 커밋**

```bash
git add .github/workflows/daily-extract-reasons.yml
git commit -m "feat: daily-extract-reasons 워크플로우 — KST 05:00 이유 추출 배치"
```

---

### Task 6: E2E 검증 — 추천 RPC 테스트

마이그레이션 + 초기 배치 완료 후, 실제 추천이 작동하는지 검증.

**Files:**
- Create: `scripts/test_recommendation_e2e.py` (일회성 검증 스크립트)

- [ ] **Step 1: E2E 검증 스크립트 작성**

```python
# scripts/test_recommendation_e2e.py
"""추천 v2 E2E 검증 — 실제 DB 데이터로 추천 파이프라인 테스트.

1. 테스트 유저의 taste_reasons 생성 (mock)
2. recommend_books_by_reasons RPC 호출
3. 결과 검증: 피드백이 다르면 추천이 다른지
"""

import json
import os
import sys
import uuid

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from lib.openai_helpers import call_chat, call_embedding
from reason_extractor import build_feedback_prompt, parse_reasons, filter_generic_reasons

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def create_test_taste_reasons(user_id, feedback_text, book_id, rating_weight=1.0):
    """피드백에서 taste_reasons를 생성하고 DB에 저장."""
    raw = call_chat(build_feedback_prompt(feedback_text))
    reasons = filter_generic_reasons(parse_reasons(raw))
    if not reasons:
        print(f"  이유 추출 실패: '{feedback_text[:30]}'")
        return []

    embeddings = call_embedding(reasons)
    rows = []
    for reason, emb in zip(reasons, embeddings):
        rows.append({
            "user_id": str(user_id),
            "book_id": book_id,
            "reason": reason,
            "reason_embedding": emb,
            "weight": rating_weight,
        })
    sb.table("user_taste_reasons").insert(rows).execute()
    return reasons


def get_recommendations(user_id, count=10):
    """RPC 호출."""
    r = sb.rpc("recommend_books_by_reasons", {
        "p_user_id": str(user_id),
        "p_match_count": count,
    }).execute()
    return r.data


def cleanup(user_id):
    """테스트 데이터 정리."""
    sb.table("user_taste_reasons").delete().eq("user_id", str(user_id)).execute()


def run_test():
    harry_potter_id = "76cd5e63-29de-4ca4-8414-9ee035e6aef0"

    # 유저 A: 세계관 피드백
    user_a = uuid.uuid4()
    print(f"\n=== 유저 A: 세계관 중시 ===")
    reasons_a = create_test_taste_reasons(
        user_a, "세계관이 새롭고 디테일하고 생동감있어서 몰입이 되었어", harry_potter_id
    )
    print(f"  추출된 이유: {reasons_a}")
    recs_a = get_recommendations(user_a)
    print(f"  추천 Top 5:")
    for i, r in enumerate(recs_a[:5], 1):
        print(f"    {i}. [{r['score']:.3f}] {r['title'][:35]} ← {r['matched_reason'][:30]}")

    # 유저 B: 캐릭터 피드백
    user_b = uuid.uuid4()
    print(f"\n=== 유저 B: 캐릭터 우정 중시 ===")
    reasons_b = create_test_taste_reasons(
        user_b, "캐릭터들의 성장과 우정이 감동적이었어", harry_potter_id
    )
    print(f"  추출된 이유: {reasons_b}")
    recs_b = get_recommendations(user_b)
    print(f"  추천 Top 5:")
    for i, r in enumerate(recs_b[:5], 1):
        print(f"    {i}. [{r['score']:.3f}] {r['title'][:35]} ← {r['matched_reason'][:30]}")

    # 비교
    titles_a = {r["title"] for r in recs_a[:5]}
    titles_b = {r["title"] for r in recs_b[:5]}
    overlap = titles_a & titles_b
    diff = (titles_a - titles_b) | (titles_b - titles_a)
    print(f"\n=== 결과 비교 ===")
    print(f"  겹치는 책: {len(overlap)}권")
    print(f"  다른 책: {len(diff)}권")
    print(f"  → {'성공: 피드백이 다르면 추천이 다름' if diff else '실패: 추천이 동일'}")

    # 정리
    cleanup(user_a)
    cleanup(user_b)


if __name__ == "__main__":
    run_test()
```

- [ ] **Step 2: E2E 실행**

```bash
python3 scripts/test_recommendation_e2e.py
```

Expected: 유저 A와 B의 Top 5 추천에서 2~3권 이상 차이.

- [ ] **Step 3: 커밋**

```bash
git add scripts/test_recommendation_e2e.py
git commit -m "test: 추천 v2 E2E 검증 — 피드백별 추천 차이 확인"
```

---

### Task 7: ARCHITECTURE.md 동기화

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: ARCHITECTURE.md에 v2 추가 사항 반영**

아래 내용을 해당 섹션에 추가:
- 테이블 상세에 `book_love_reasons`, `user_taste_reasons` 추가
- 배치 파이프라인에 `daily-extract-reasons` (05:00 KST) 추가
- RPC 목록에 `recommend_books_by_reasons` 추가
- `recommend_books_for_user` → 대체 예정 표시
- 데이터 흐름에 reason 추출/매칭 경로 추가

- [ ] **Step 2: 커밋**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: ARCHITECTURE.md — 추천 v2 테이블, RPC, 파이프라인 동기화"
```

---

## 실행 순서 요약

```
Task 1: OpenAI 헬퍼         ← 의존 없음
Task 2: DB 마이그레이션       ← 의존 없음 (Supabase에서 실행)
Task 3: Reason Extractor    ← Task 1 의존
Task 4: 초기 배치 실행        ← Task 2, 3 의존
Task 5: GitHub Actions      ← Task 3 의존
Task 6: E2E 검증            ← Task 2, 3, 4 의존
Task 7: ARCHITECTURE 동기화  ← 의존 없음
```

Task 1 + 2 + 7은 병렬 가능. Task 3은 Task 1 후. Task 4~6은 순차.
