"""v1 Reason 추출 — 레거시 경로.

⚠️ 2026-04-10 이후 orchestrator 는 이 스크립트를 호출하지 않는다.
   메인 경로는 scripts/v3_reason_extract.py (source='v3_context_rich').

이 스크립트는:
- source='llm_extracted' 로 저장 (v1 format)
- 공존 가능: book_love_reasons UNIQUE (book_id, source, reason) 덕분
- Eden 이 legacy data 를 re-extract 할 때만 수동 실행

삭제 후보이지만 기존 v1 데이터 분석용으로 당분간 보존.

사용법:
  python3 scripts/reason_extractor.py                  # 미처리분
  python3 scripts/reason_extractor.py --limit 100      # 최대 100권
  python3 scripts/reason_extractor.py --dry-run        # DB 저장 없이 테스트
  python3 scripts/reason_extractor.py --status          # 커버리지 현황
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

try:
    from lib.openai_helpers import call_chat, call_embedding
except ImportError:
    pass  # 테스트 환경에서는 순수 함수만 사용

# lib.retry 는 hard dependency. silent no-op fallback 금지.
# (과거: 패스 문제로 retry 가 no-op 되어 수백 건 reason drop 하고도
#  exit 0 으로 끝나는 사고가 있었음.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402

MIN_REASON_LENGTH = 2
BATCH_SIZE = 20  # 배치 크기 (insert는 3행씩이라 안정적)
PARALLEL_WORKERS = 10  # LLM 병렬 호출 수
EMBEDDING_BATCH_SIZE = 20  # 임베딩 일괄 처리 크기 (타임아웃 방지)

# 범용 표현 패턴 — 구체적이지 않은 이유 필터링용 (부분일치)
GENERIC_PATTERNS = [
    "재밌", "감동적", "좋은 책", "읽어볼 만", "추천",
    "괜찮", "잘 읽힌", "흥미롭", "인상적", "소장 가치",
    "몰입감", "흡입력", "정점", "걸작", "수작", "명작",
]

# 메타 카테고리 — 장르 분류 체계 자체는 취향 축이 아님
META_CATEGORIES = [
    "국내도서", "해외도서", "소설", "에세이", "인문학", "교양 인문학",
    "만화", "라이트노벨", "명사에세이", "방송연예인에세이",
    "자기계발", "경제경영", "과학", "사회과학",
]



# ──────────────────────────────────────────────
# 순수 함수 (API 호출 없이 테스트 가능)
# ──────────────────────────────────────────────

def extract_key_terms(rich_description):
    """출판사가 강조한 텍스트(<b> 태그)와 따옴표로 감싼 핵심 용어를 추출."""
    if not rich_description:
        return []

    terms = []

    # 1. <b> 태그 안의 텍스트 (출판사 강조)
    for m in re.finditer(r"<[Bb]>(.*?)</[Bb]>", rich_description, re.DOTALL):
        clean = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        # 여러 줄이면 분리
        for phrase in re.split(r"\s{2,}", clean):
            phrase = phrase.strip()
            if phrase and 2 <= len(phrase) <= 80:
                terms.append(phrase)

    # 2. 따옴표/홑따옴표로 감싼 용어 (핵심 개념)
    clean_text = re.sub(r"<[^>]+>", "", rich_description)
    for q in re.findall(r"['\u2018\u2019]([^'\u2018\u2019]+)['\u2018\u2019]", clean_text):
        if 2 <= len(q) <= 50:
            terms.append(q)

    # 중복 제거 (순서 유지)
    seen = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def build_step1_prompt(title, genre, description, library_keywords):
    """1차 프롬프트: 원문에서 취향 관련 키워드를 빠짐없이 나열."""
    parts = [f"도서 제목: {title}"]
    if genre:
        parts.append(f"장르: {genre}")
    if description:
        clean_desc = re.sub(r"<[^>]+>", "", description)
        parts.append(f"설명:\n{clean_desc}")
    if library_keywords:
        parts.append(f"키워드: {', '.join(library_keywords)}")
    book_info = "\n".join(parts)

    return f"""아래 도서 설명에서 독자의 취향과 매칭될 수 있는 키워드를 나열해주세요.
각 키워드는 "이런 걸 좋아하는 독자"가 존재할 수 있는 취향 축이어야 합니다.

{book_info}

## 뽑을 것 (취향 축)
- 장르/하위장르: "사회파 미스터리", "생물학적 SF", "휴먼 미스터리"
- 테마: "퀴어", "공생", "성장", "정체성", "이종교배"
- 분위기/톤: "서늘한 긴장감", "유쾌함", "고독"
- 소재/설정: "핵전쟁", "기억 상실", "이세계", "요괴"
- 시리즈명: 후속권 매칭에 필요 (예: "위처 시리즈")

## 제외할 것
- 이 책의 제목, 소제목, 챕터명
- 고유명사 (캐릭터명, 저자명, 출판사명, 외계종족명)
- 다른 작품 제목
- 마케팅 문구 ("최초 완역", "1억 부", "베스트셀러")
- 평론 표현 ("정점", "걸작", "흡입력", "몰입감")
- 플롯 디테일 (특정 사물, 장면, "권총", "편지", "맨션")
- "vs" 비교 문구 ("A vs B" 형태)
- 질문형 문구 ("왜 ~인가", "~할까?")
- 문장 (주어+서술어 구조, "나는 혼자가 아니다" 등)
- 메타 카테고리 ("국내도서", "소설", "에세이", "인문학", "교양 인문학")
- 너무 일반적인 단어 ("인간", "삶", "시간", "주인공", "감정")

## 형식 규칙
- 각 키워드는 2~6단어의 명사구
- 1 키워드 = 1 개념. 절대 합치지 말 것.
- 원문에 있는 표현만. 없는 것 만들지 말 것.

## 예시 (good vs bad)

예시1) "완벽한 원시인 - 10만 년을 되돌려 되찾는 뇌 설계도" (인문학)
- GOOD: ["진화심리학", "뇌과학", "셀프케어", "건강 습관", "원시 생활방식", "자기 최적화", "과학 기반 자기계발"]
- BAD: ["완벽한 원시인", "하루 2리터 물 마시기", "공복 운동", "5단계 레벨 시스템"] ← 제목/방법론/구성은 취향 축 아님

예시2) "유럽 도시 기행" (인문학)
- GOOD: ["여행 인문학", "도시 역사 탐방", "건축 문화", "지적인 여행", "유럽 문화"]
- BAD: ["아테네", "로마", "파리"] ← 지명은 고유명사. ["텍스트", "콘텍스트"] ← 너무 일반적

예시3) "새벽" (SF소설)
- GOOD: ["이종교배", "퀴어", "공생", "생물학적 SF", "정체성", "외계 문명과의 접촉"]
- BAD: ["오안칼리", "릴리스"] ← 작품 내 고유명사, 안 읽은 사람에게 매칭 불가

JSON: {{"keywords": ["키워드1", "키워드2", ...]}}"""


def build_step2_prompt(title, genre, keywords):
    """2차 프롬프트: 키워드를 취향 매칭용 reason으로 정리."""
    return f"""아래 키워드들은 "{title}" ({genre})에서 추출한 것입니다.
각 키워드를 하나씩 판단하여 keep 또는 drop으로 분류하세요.

키워드: {', '.join(keywords)}

## drop 기준 (하나라도 해당하면 제거)
- 이 책의 제목, 소제목, 챕터명
- 고유명사 (캐릭터명, 저자명, 출판사명, 외계종족명)
- 다른 작품 제목
- 플롯 디테일 (특정 사물: "권총", "현금", "주검", "맨션", "편지")
- 메타 카테고리 ("국내도서", "소설", "에세이", "인문학", "교양 인문학", "만화", "라이트노벨", "명사에세이", "방송연예인에세이")
- 평론 표현 ("흡입력", "몰입감", "소장 가치", "정점", "걸작")
- 너무 일반적인 단어 ("인간", "인물", "주인공", "시간", "여유", "기분", "삶", "감정", "텍스트")
- 질문형/문장형 ("왜 ~인가", "~할까?", 주어+서술어 구조)
- "vs" 비교 문구 ("A vs B")
- 이 책의 구체적 방법론/구성 ("5단계 시스템", "3장 구성" 등)

## keep 기준 ("이런 걸 좋아하는 독자"가 존재할 수 있는 취향 축)
- 장르/하위장르 ("사회파 미스터리", "생물학적 SF")
- 테마 ("퀴어", "공생", "성장", "이종교배", "정체성")
- 분위기/톤 ("서늘한 긴장감", "유쾌함", "오싹함", "고독")
- 소재/설정 ("핵전쟁", "기억 상실", "이세계", "요괴")
- 시리즈명 (후속권 매칭용)

## 절대 규칙
- 키워드를 합치거나 재작성하지 말 것. 원본 그대로 keep 또는 drop만.
- 새 키워드를 만들지 말 것.

JSON: {{"reasons": ["키워드1", "키워드2", ...]}}"""


def build_extraction_prompt(title, genre, description, library_keywords,
                            rich_description_raw=None):
    """하위 호환: 기존 인터페이스 유지. 내부적으로 step1 프롬프트 반환."""
    return build_step1_prompt(title, genre, description, library_keywords)


def build_feedback_select_prompt(feedback_text, book_title, book_reasons):
    """브릿지 매칭: 피드백 + 좋아한 책의 reason 목록 → 관련 reason 선택.

    유저 피드백에서 새 reason을 추출하지 않고,
    좋아한 책의 기존 reason 중 피드백과 관련 있는 것을 선택한다.
    선택된 reason은 유저의 취향 마커로 저장되어
    태그↔태그 임베딩 매칭으로 다른 책을 추천하는 데 사용된다.
    """
    reasons_list = "\n".join(f"- {r}" for r in book_reasons)
    return f"""사용자가 아래 책에 대해 피드백을 남겼습니다.
이 피드백을 보고, 아래 reason 목록에서 피드백과 관련 있는 것을 골라주세요.

책: {book_title}
피드백: {feedback_text}

reason 목록:
{reasons_list}

규칙:
- 목록에 있는 것만 고를 것. 새로 만들지 말 것.
- 피드백이 언급하거나 암시하는 것과 관련 있는 reason만.
- 1~4개 선택.

JSON: {{"selected": ["reason1", "reason2", ...]}}"""


def build_feedback_prompt(feedback_text):
    """[DEPRECATED] build_feedback_select_prompt를 대신 사용할 것."""
    prompt = f"""사용자가 책에 대해 남긴 피드백입니다. 이 피드백에서 독립적인 취향 키워드를 추출해주세요.

피드백: {feedback_text}

## 추출 규칙
- 피드백에서 직접 언급하거나 강하게 암시하는 것만
- 1 reason = 1 concept (독립적으로 쪼갤 것)
- 2~6단어의 명사구로 표현
- 피드백이 너무 모호하면 빈 리스트 반환

## 뽑을 것 (취향 축)
- 장르/하위장르: "사회파 미스터리", "퀴어 소설", "SF"
- 문체/스타일: "섬세한 심리묘사", "건조한 문체", "서정적 문체"
- 분위기/톤: "서늘한 긴장감", "따뜻한 위로", "유쾌한 유머"
- 테마/소재: "기억 상실", "정체성 탐구", "가족 관계"
- 서사 구조: "반전", "복선 회수", "비선형 서사"

## 제외할 것
- 범용 감상: "재밌다", "좋았다", "독특했다", "인상적이다", "감동적이다"
- 평론 표현: "몰입감", "흡입력", "걸작", "수작"
- 모든 책에 해당할 수 있는 말은 취향 축이 아님

## 예시
- "심리묘사가 섬세해서 좋았다" → ["섬세한 심��묘사"]
- "퀴어 소설인데 SF적 상상력이 독특했다" → ["퀴어", "SF적 상상력"]
- "반전이 소름 돋았고 복선 회수가 잘 됐다" → ["반전", "복선 회수"]
- "그냥 좋았어요" → []

JSON: {{"reasons": ["키워드1", "키워드2", ...]}}"""

    return prompt


def parse_reasons(raw_response):
    """LLM JSON 응답에서 (reason, evidence) 쌍 추출.

    반환: [{"reason": str, "evidence": str}, ...]
    evidence가 없는 형식도 호환 (evidence="").
    """
    if not isinstance(raw_response, dict):
        return []
    reasons = raw_response.get("reasons", [])
    if not isinstance(reasons, list):
        return []
    result = []
    for r in reasons:
        if isinstance(r, dict):
            text = r.get("reason", "").strip()
            evidence = r.get("evidence", r.get("source", "")).strip()
        elif isinstance(r, str):
            text = r.strip()
            evidence = ""
        else:
            continue
        if text:
            result.append({"reason": text, "evidence": evidence})
    return result


def verify_reasons(reasons, source_text):
    """evidence 기반 2차 검증. 근거 없는 reason 탈락.

    검증 방법:
    1. evidence가 있으면 → evidence 내 핵심 명사(2자 이상)가 source_text에 존재하는지 체크
    2. evidence가 없으면 → reason 자체의 핵심 명사로 체크
    3. 매칭률 40% 미만이면 탈락

    반환: 검증 통과한 reason 텍스트 리스트
    """
    import re as _re
    # 원본 텍스트 정규화 (HTML 태그 제거, 소문자화)
    clean = _re.sub(r"<[^>]+>", "", source_text)

    verified = []
    for item in reasons:
        reason = item["reason"]
        evidence = item["evidence"]

        # 검증 대상 텍스트 (evidence 우선, 없으면 reason)
        check_text = evidence if evidence else reason

        # evidence가 있으면: evidence 문자열이 원본에 포함되는지 (substring 체크)
        if evidence:
            # evidence 원문이 source에 있으면 통과 (가장 확실한 검증)
            if evidence in clean:
                verified.append(reason)
                continue
            # evidence 원문 그대로는 없지만, 핵심 단어 40% 이상 매칭이면 통과
            words = [w for w in evidence.split() if len(w) >= 2]
            if words:
                found = sum(1 for w in words if w in clean)
                if found / len(words) >= 0.4:
                    verified.append(reason)
                    continue
        else:
            # evidence 없으면 reason 자체로 체크
            if reason in clean:
                verified.append(reason)
                continue
            words = [w for w in reason.split() if len(w) >= 2]
            if words:
                found = sum(1 for w in words if w in clean)
                if found / len(words) >= 0.4:
                    verified.append(reason)
        # else: 탈락 (로그는 호출측에서 필요 시)

    return verified


def filter_generic_reasons(reasons):
    """범용/모호한 표현 + 메타 카테고리 + 문장형/질문형 필터링."""
    filtered = []
    for reason in reasons:
        r = reason.strip()
        # 길이 필터
        if len(r) < MIN_REASON_LENGTH:
            continue
        # 범용 표현 필터 (부분일치)
        if any(p in r for p in GENERIC_PATTERNS):
            continue
        # 메타 카테고리 필터 (완전일치)
        if r in META_CATEGORIES:
            continue
        # 질문형 필터
        if r.endswith("?") or r.endswith("할까") or r.endswith("인가"):
            continue
        # 문장형 필터 (주어+서술어: ~다, ~요 로 끝나는 것)
        if re.match(r".+(?:이다|한다|된다|있다|없다|했다|됐다|아니다)$", r):
            continue
        # 명사형 종결 문장 필터 (문장을 ~함/~냄/~임 으로 명사화한 것, 12자 초과)
        if len(r) > 12 and re.search(r"[을를의에서로].*[냄함임음줌짐김됨봄럼침]$", r):
            continue
        # 과도한 길이 필터 (25자 초과 = 문장일 가능성 높음)
        if len(r) > 25:
            continue
        # "vs" 비교 문구 필터
        if " vs " in r or " VS " in r:
            continue
        filtered.append(reason)
    return filtered


# ──────────────────────────────────────────────
# ReasonExtractor 클래스 (파이프라인)
# ──────────────────────────────────────────────

class ReasonExtractor:
    def __init__(self, sb, dry_run=False, rerun=False):
        self.sb = sb
        self.dry_run = dry_run
        self.rerun = rerun
        self.stats = {
            "processed": 0,           # 성공한 책 수
            "reasons_created": 0,      # 생성된 reason row 수 (성공한 것만)
            "skipped": 0,              # LLM 이 빈 결과 반환한 책 수
            # --- 실패 카운터 (단위 분리) ---
            "errors_books": 0,         # 책 단위 실패: LLM 실패 + 임베딩 손실
            "errors_rows": 0,          # row 단위 실패: insert 실패 reason row 수
            "deleted": 0,
        }


    def run(self, limit=None):
        """메인 배치 루프: 이유 미추출 도서 조회 → 추출 → 임베딩 → 저장."""
        if self.rerun:
            return self._run_rerun(limit)

        print("🔍 이유 미추출 도서 조회 중...")

        # 이미 처리된 book_id 수집
        processed_ids = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: self.sb.table("book_love_reasons")
                .select("book_id")
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            for row in result.data:
                processed_ids.add(row["book_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        # 1단계: rich_description NOT NULL인 도서 ID만 먼저 조회 (가벼움)
        candidate_ids = []
        offset = 0
        while True:
            result = with_retry(lambda o=offset: self.sb.table("books")
                .select("id")
                .not_.is_("rich_description", "null")
                .order("sales_point", desc=True)
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            candidate_ids.extend(r["id"] for r in result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

        # 이미 처리된 도서 제외
        target_ids = [bid for bid in candidate_ids if bid not in processed_ids]
        not_ready_count = with_retry(lambda: self.sb.table("books")
            .select("id", count="exact")
            .is_("rich_description", "null")
            .execute()).count or 0

        # 2단계: 대상 도서 상세 조회 (200건씩, rich_description 포함)
        fetch_size = 200
        ready = []
        for i in range(0, len(target_ids), fetch_size):
            chunk_ids = target_ids[i:i + fetch_size]
            result = with_retry(lambda ids=chunk_ids: self.sb.table("books")
                .select("id, title, genre, description, rich_description, library_keywords, sales_point")
                .in_("id", ids)
                .execute())
            if result.data:
                ready.extend(result.data)
        # sales_point 순서 복원
        id_order = {bid: idx for idx, bid in enumerate(target_ids)}
        ready.sort(key=lambda b: id_order.get(b["id"], 999999))

        if limit:
            ready = ready[:limit]

        print(f"   {len(ready)}권 대상 (처리 완료: {len(processed_ids)}권, 데이터 미충족: {not_ready_count}권)\n")
        books = ready

        if not books:
            print("✅ 이유 추출이 필요한 도서가 없습니다.")
            return

        # 배치 단위로 처리: LLM 병렬 추출 → 임베딩 일괄 → DB 일괄 저장
        for batch_start in range(0, len(books), BATCH_SIZE):
            batch = books[batch_start:batch_start + BATCH_SIZE]
            self._process_batch(batch)
            done = min(batch_start + BATCH_SIZE, len(books))
            print(f"  ... {done}/{len(books)}권 처리 완료")

        self.print_report()

    def _run_rerun(self, limit=None):
        """Re-run 모드: 기존 reason이 있는 도서를 삭제 후 재추출."""
        print("🔄 Re-run 모드: 기존 reason 삭제 후 재추출")

        # rich_description 있는 도서 ID를 books 테이블에서 가져옴 (가벼움)
        # book_love_reasons는 2000차원 벡터라 SELECT조차 느림
        candidate_ids = []
        offset = 0
        page_size = 500
        fetch_limit = limit or 99999
        while len(candidate_ids) < fetch_limit:
            result = with_retry(lambda o=offset: self.sb.table("books")
                .select("id")
                .not_.is_("rich_description", "null")
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            candidate_ids.extend(r["id"] for r in result.data)
            if len(result.data) < page_size:
                break
            offset += page_size

        target_ids = candidate_ids[:fetch_limit]
        print(f"   대상: {len(target_ids)}권")

        # 도서 상세 조회
        books = []
        for i in range(0, len(target_ids), 200):
            chunk = target_ids[i:i + 200]
            result = with_retry(lambda ids=chunk: self.sb.table("books")
                .select("id, title, genre, description, rich_description, library_keywords")
                .in_("id", ids)
                .execute())
            if result.data:
                books.extend(result.data)

        if not books:
            print("✅ 재추출 대상이 없습니다.")
            return

        # 배치 단위로: 기존 삭제 → 재추출 → 저장
        for batch_start in range(0, len(books), BATCH_SIZE):
            batch = books[batch_start:batch_start + BATCH_SIZE]
            batch_ids = [b["id"] for b in batch]

            # 기존 reason 삭제 (IN 쿼리로 배치 삭제)
            if not self.dry_run:
                try:
                    with_retry(lambda ids=batch_ids: self.sb.table("book_love_reasons")
                        .delete()
                        .in_("book_id", ids)
                        .execute())
                    self.stats["deleted"] += len(batch_ids)
                except Exception:
                    # 배치 실패 시 1건씩
                    for bid in batch_ids:
                        try:
                            with_retry(lambda b=bid: self.sb.table("book_love_reasons")
                                .delete()
                                .eq("book_id", b)
                                .execute())
                            self.stats["deleted"] += 1
                        except Exception as e:
                            print(f"  ✗ 삭제 실패 {bid[:8]}: {e}")

            # 재추출 + 저장
            self._process_batch(batch)
            done = min(batch_start + BATCH_SIZE, len(books))
            print(f"  ... {done}/{len(books)}권 처리 완료")
            time.sleep(1)  # 배치 간 connection 안정화

        self.print_report()

    def _process_batch(self, batch):
        """배치 단위 처리: LLM 병렬 → 임베딩 일괄 → DB 일괄 저장."""
        # 1단계: LLM으로 이유 추출 (병렬)
        extracted = {}  # book_id → reasons list
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = {pool.submit(self._extract_reasons, book): book for book in batch}
            for future in as_completed(futures):
                book = futures[future]
                try:
                    reasons = future.result()
                    if reasons:
                        extracted[book["id"]] = (book, reasons)
                        self.stats["processed"] += 1
                    else:
                        self.stats["skipped"] += 1
                except Exception as e:
                    self.stats["errors_books"] += 1
                    print(f"  ✗ [{book.get('title', '?')[:25]}] LLM 실패: {e}")

        if not extracted:
            return

        # 2단계: 모든 이유를 모아서 임베딩 일괄 호출
        all_reasons = []
        reason_map = []  # (book_id, reason_index)
        for book_id, (book, reasons) in extracted.items():
            for r in reasons:
                all_reasons.append(r)
                reason_map.append(book_id)

        # 임베딩 API 배치 제한 대응 (50개씩, chunk 단위 fallback)
        all_embeddings = [None] * len(all_reasons)
        for i in range(0, len(all_reasons), EMBEDDING_BATCH_SIZE):
            chunk = all_reasons[i:i + EMBEDDING_BATCH_SIZE]
            try:
                embs = call_embedding(chunk)
                for j, emb in enumerate(embs):
                    all_embeddings[i + j] = emb
            except Exception as e:
                print(f"  ✗ 임베딩 chunk 실패 ({i}~{i+len(chunk)}): {e}")

        # 임베딩 성공한 것만 필터링
        valid_indices = [i for i, emb in enumerate(all_embeddings) if emb is not None]
        failed_count = len(all_reasons) - len(valid_indices)
        if failed_count > 0:
            print(f"  ⚠ 임베딩 {failed_count}건 실패, {len(valid_indices)}건 진행")
        if not valid_indices:
            self.stats["errors_books"] += len(extracted)
            return

        all_reasons = [all_reasons[i] for i in valid_indices]
        all_embeddings = [all_embeddings[i] for i in valid_indices]
        reason_map = [reason_map[i] for i in valid_indices]

        # 임베딩 실패로 이유가 모두 사라진 책 → stats 보정
        surviving_book_ids = set(reason_map)
        lost_book_ids = set(extracted.keys()) - surviving_book_ids
        if lost_book_ids:
            self.stats["errors_books"] += len(lost_book_ids)
            self.stats["processed"] -= len(lost_book_ids)

        # 3단계: DB 일괄 저장
        if not self.dry_run:
            rows = []
            for idx, (reason, embedding) in enumerate(zip(all_reasons, all_embeddings)):
                rows.append({
                    "book_id": reason_map[idx],
                    "reason": reason,
                    "reason_embedding": embedding,
                    "source": "llm_extracted",
                })
            # Supabase 배치 insert (20행씩 → 실패 시 5행씩 mini-fallback)
            #
            # 왜 20 → 5 단계로 쪼개는가:
            #   reason row 하나에 `reason_embedding` (1536-dim vector) 가 박히기 때문에
            #   20행 insert 는 payload 가 꽤 크고 DB statement_timeout (57014) 에 자주 걸린다.
            #   5행으로 쪼개면 statement 가 훨씬 빨라 timeout 을 회피할 수 있다.
            #   retry.py 가 57014 를 backoff 재시도하므로 쪼개기 + 재시도가 결합돼
            #   일시적 DB 부하를 견딘다.
            INSERT_BATCH = 20
            MINI_BATCH = 5
            for i in range(0, len(rows), INSERT_BATCH):
                chunk = rows[i:i + INSERT_BATCH]
                try:
                    with_retry(lambda c=chunk: self.sb.table("book_love_reasons")
                        .upsert(c, on_conflict="book_id,source,reason",
                                ignore_duplicates=True).execute())
                except Exception:
                    # 배치 실패 시 더 작은 단위로 쪼개서 재시도 (retry.py 도 함께 backoff)
                    for j in range(0, len(chunk), MINI_BATCH):
                        mini = chunk[j:j + MINI_BATCH]
                        try:
                            with_retry(lambda m=mini: self.sb.table("book_love_reasons")
                                .upsert(m, on_conflict="book_id,source,reason",
                                        ignore_duplicates=True).execute())
                        except Exception as e2:
                            print(f"  ✗ insert 실패 ({len(mini)}행): {e2}")
                            self.stats["errors_rows"] += len(mini)

        # 실제 저장된 이유 수 기준으로 카운트
        self.stats["reasons_created"] += len(all_reasons)
        # 저장 성공한 책별 이유 수 계산
        saved_counts = {}
        for book_id in reason_map:
            saved_counts[book_id] = saved_counts.get(book_id, 0) + 1
        for book_id, (book, reasons) in extracted.items():
            prefix = "(dry-run) " if self.dry_run else ""
            saved = saved_counts.get(book_id, 0)
            if saved > 0:
                print(f"  {prefix}✓ [{book['title'][:25]}] {saved}개")
            else:
                print(f"  {prefix}✗ [{book['title'][:25]}] 임베딩 실패로 스킵")

    def _extract_reasons(self, book):
        """2단계 LLM 추출: 1차 키워드 나열 → 2차 취향 reason 정리."""
        title = book.get("title", "")
        genre = book.get("genre", "")
        description = book.get("description", "")
        rich_desc = book.get("rich_description")

        if rich_desc:
            clean_rich = re.sub(r"<[^>]+>", "", rich_desc)
            if len(clean_rich) > len(description or ""):
                description = clean_rich

        # 1차: 원문에서 취향 키워드 빠짐없이 나열
        step1_prompt = build_step1_prompt(
            title, genre, description, book.get("library_keywords")
        )
        step1_raw = call_chat(step1_prompt, temperature=0)
        keywords = step1_raw.get("keywords", [])
        if not keywords:
            return None

        # 2차: 키워드를 취향 매칭용 reason으로 정리
        step2_prompt = build_step2_prompt(title, genre, keywords)
        step2_raw = call_chat(step2_prompt, temperature=0)
        reasons = step2_raw.get("reasons", [])
        if not isinstance(reasons, list):
            return None

        # 후처리 필터링
        reasons = [r for r in reasons if isinstance(r, str) and r.strip()]
        reasons = filter_generic_reasons(reasons)

        # 책 제목/소제목 필터 — 제목 자체는 취향 축이 아님
        title_parts = re.split(r"\s*[-–—:]\s*", title)
        reasons = [r for r in reasons
                   if r not in title_parts and r != title]

        return reasons if reasons else None

    def print_report(self):
        """배치 결과 출력."""
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}Reason Extractor 결과")
        print(f"{'=' * 50}")
        if s.get("deleted", 0) > 0:
            print(f"  기존 삭제: {s['deleted']}권")
        print(f"  처리 완료: {s['processed']}권")
        print(f"  이유 생성: {s['reasons_created']}건")
        print(f"  스킵 (이유 없음): {s['skipped']}권")
        print(f"  실패 (책 단위): {s['errors_books']}권")
        print(f"  실패 (row 단위): {s['errors_rows']}건")
        print(f"{'=' * 50}")

    @staticmethod
    def get_status(sb):
        """커버리지 현황 출력."""
        total_books = with_retry(lambda: sb.table("books")
            .select("id", count="exact")
            .execute())

        # book_love_reasons에서 고유 book_id 수 조회
        reason_book_ids = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: sb.table("book_love_reasons")
                .select("book_id")
                .range(o, o + page_size - 1)
                .execute())
            if not result.data:
                break
            for row in result.data:
                reason_book_ids.add(row["book_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        total_reasons = with_retry(lambda: sb.table("book_love_reasons")
            .select("id", count="exact")
            .execute())

        covered = len(reason_book_ids)
        total = total_books.count or 0
        pct = (covered / total * 100) if total > 0 else 0

        print(f"\n{'=' * 50}")
        print("Reason Extractor 현황")
        print(f"{'=' * 50}")
        print(f"  전체 도서: {total}권")
        print(f"  이유 추출 완료: {covered}권 ({pct:.1f}%)")
        print(f"  총 이유 수: {total_reasons.count}건")
        print(f"  미추출: {total - covered}권")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="Reason Extractor — 좋아할 이유 추출")
    parser.add_argument("--limit", type=int, default=None, help="최대 처리 권수")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="커버리지 현황 조회")
    parser.add_argument("--rerun", action="store_true",
                        help="기존 reason 삭제 후 재추출 (프롬프트 개선 시 사용)")
    args = parser.parse_args()

    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )

    if args.status:
        ReasonExtractor.get_status(sb)
        return 0

    extractor = ReasonExtractor(sb, dry_run=args.dry_run, rerun=args.rerun)
    extractor.run(limit=args.limit)

    # 실패 판정 정책 (단위 분리):
    #   errors_books (책 단위 — LLM 실패 / 임베딩 손실) > 0  → 항상 fail
    #     이유: 책이 통째로 reason 없는 상태로 남으면 추천에서 invisible
    #   errors_rows (row 단위 — insert 실패) 비율 > 10%  → fail
    #     이유: row 일부 drop 은 일시적 DB 부하 가능. 10% 이상이면 비정상.
    s = extractor.stats
    eb = s.get("errors_books", 0)
    er = s.get("errors_rows", 0)
    created = s.get("reasons_created", 0)

    if eb > 0:
        print(f"⚠ 책 단위 실패 {eb}권 (LLM/임베딩) — 재실행 권장 (idempotent)")
        return 1
    if er > 0 and (created + er) > 0:
        ratio = er / (created + er)
        if ratio > 0.10:
            print(
                f"⚠ row 실패율 {ratio*100:.1f}% ({er}/{created + er}) — "
                f"10% 초과, 재실행 권장"
            )
            return 1
        else:
            # 일부 row 실패는 허용하되 로그는 남김
            print(f"  ℹ row 실패 {er}건 ({ratio*100:.1f}%) — 임계 내")
    return 0


if __name__ == "__main__":
    sys.exit(main())
