"""
도서 중복 제거 스크립트 — 같은 작품의 다른 에디션을 소프트 머지

문제: 같은 책이 여러 에디션으로 DB에 존재
  예) "1984" vs "1984 (오리지널 초판본 표지 디자인)"
      "동물농장" vs "동물농장 (양장)" vs "동물농장 (특별판)"

전략:
  1. 제목 정규화 → 동일 저자 그룹핑 → 중복 그룹 식별
  2. 시리즈물은 제외 (부제가 내용을 구분하는 경우)
  3. 정본(canonical) 선정 → 나머지에 canonical_book_id 마킹

사용법:
  python3 scripts/dedup_books.py                  # 미리보기 (기본값)
  python3 scripts/dedup_books.py --apply           # 실제 DB 반영
  python3 scripts/dedup_books.py --verbose         # 상세 출력
"""

import argparse
import os
import re
import sys
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ── 시리즈 판별 패턴 ──────────────────────────────────────
# 콜론/대시 뒤의 부제가 "내용 구분"인 경우 → 시리즈물, 중복 아님
# 예: "스티커북 : 동물" vs "스티커북 : 마트" → 다른 책
SERIES_SUBTITLE_INDICATORS = [
    # 숫자 권수 패턴 (1권, 2권, vol.1 등)
    r"^\s*\d+권?$",
    r"^\s*(?:vol|Vol|VOL)\.?\s*\d+",
    r"^\s*(?:제?\d+\s*(?:권|편|부|장|화|탄))$",
    # 명확한 내용 구분 부제 (한글 2~6자 단독)
    r"^\s*[가-힣]{1,6}\s*$",
]

# 시리즈 키워드 — 이 단어가 정규화된 제목에 포함되면 시리즈일 가능성 높음
SERIES_TITLE_KEYWORDS = [
    "스티커북", "스티커", "색칠", "컬러링", "워크북", "학습지",
    "세트", "전집", "시리즈", "그림책", "놀이북", "활동북",
    "나의 첫", "토미", "톰토미",
]


def normalize_title(title: str) -> str:
    """
    중복 비교용 제목 정규화

    처리 순서:
      1. 괄호 안 에디션/판본 정보 제거
      2. 앞뒤 공백 + 연속 공백 정리
      3. 콜론/대시 뒤 부제 제거 (시리즈 판별 전에는 보존)

    주의: 시리즈 판별은 별도 함수에서 처리
    """
    normalized = title

    # 괄호 안 에디션/판본/굿즈 정보 제거
    # 예: (양장), (특별판), (오리지널 초판본 표지 디자인), (리마스터)
    paren_patterns = [
        # 명시적 에디션/판본 키워드가 있는 괄호
        r"\s*\(.*?(?:판|에디션|리커버|양장|무선|문고|기념|보너스|수록|클래식|개정|완결|특전|리마스터|초판|복간|표지|디자인|일러스트|컬러|블랙|화이트|골드|실버|미니|빅|대형|포켓|뉴|신판|증보|축약|완역|전면|번역|역|합본|세트).*?\)",
        # 연도 괄호 (출판년도)
        r"\s*\(\d{4}\)",
        # 빈 괄호 정리
        r"\s*\(\s*\)",
    ]
    for pattern in paren_patterns:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    # 공백 정리
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def strip_subtitle(title: str) -> str:
    """
    콜론/대시 뒤 부제 제거 → 핵심 제목만 추출

    예: "코스모스 : 가능한 세계들" → "코스모스"
        "명상록 - 천년의 고전" → "명상록"
    """
    # 콜론 뒤 부제 제거
    title = re.sub(r"\s*[:：]\s*.+$", "", title)
    # 대시 뒤 부제 제거 (단, 하이픈으로 연결된 복합어는 유지)
    # " - " 또는 " — " 패턴만 매칭 (공백 필수)
    title = re.sub(r"\s+[-—]\s+.+$", "", title)

    return title.strip()


def is_series_item(books_in_group: list) -> bool:
    """
    그룹 내 책들이 시리즈물인지 판별

    시리즈 판별 기준:
      1. 정규화된 제목에 시리즈 키워드 포함
      2. 부제가 내용을 구분하는 역할 (권수, 주제어 등)
      3. 원본 제목의 부제 부분이 서로 다르고, 내용 구분성이 있음
    """
    if len(books_in_group) < 2:
        return False

    # 시리즈 키워드 체크
    sample_title = books_in_group[0]["title"].lower()
    for kw in SERIES_TITLE_KEYWORDS:
        if kw in sample_title:
            return True

    # 부제 추출 후 분석
    subtitles = []
    for book in books_in_group:
        title = normalize_title(book["title"])
        # 콜론이나 대시 뒤의 부제 추출
        match = re.search(r"(?:\s*[:：]\s*|\s+[-—]\s+)(.+)$", title)
        if match:
            subtitles.append(match.group(1).strip())

    # 부제가 2개 이상이고 모두 다르면 → 시리즈일 가능성
    if len(subtitles) >= 2 and len(set(subtitles)) == len(subtitles):
        # 부제가 시리즈 패턴에 매칭되는지 확인
        series_count = 0
        for sub in subtitles:
            for pattern in SERIES_SUBTITLE_INDICATORS:
                if re.match(pattern, sub):
                    series_count += 1
                    break

        # 부제 절반 이상이 시리즈 패턴이면 → 시리즈
        if series_count >= len(subtitles) * 0.5:
            return True

        # 부제가 모두 짧은 한글 (내용 구분 키워드)이면 → 시리즈
        if all(len(sub) <= 10 and re.match(r"^[가-힣\s]+$", sub) for sub in subtitles):
            return True

    return False


def compute_richness_score(book: dict) -> float:
    """
    정본 선정용 풍부도 점수 계산

    가중치:
      - description 길이 (가장 중요)
      - rich_description 존재
      - sales_point
      - page_count 존재
      - mood_tags 존재
      - library_keywords 존재
    """
    score = 0.0

    # description 길이 (max 200점)
    desc = book.get("description") or ""
    score += min(len(desc), 500) * 0.4

    # rich_description (100점)
    rich_desc = book.get("rich_description") or ""
    if rich_desc:
        score += 100

    # enriched_description (50점)
    enriched = book.get("enriched_description") or ""
    if enriched:
        score += 50

    # sales_point (max 100점)
    sp = book.get("sales_point") or 0
    score += min(sp / 1000, 100)

    # page_count (30점)
    if book.get("page_count"):
        score += 30

    # mood_tags (30점)
    if book.get("mood_tags"):
        score += 30

    # library_keywords (30점)
    if book.get("library_keywords"):
        score += 30

    # dominant_colors (20점)
    if book.get("dominant_colors"):
        score += 20

    return score


def normalize_author(author: str) -> str:
    """저자명 정규화 — 역할 표기 제거, 첫 번째 저자만"""
    if not author:
        return ""
    # (지은이), (옮긴이), (엮은이) 등 제거
    author = re.sub(r"\s*\([^)]*\)", "", author)
    # 쉼표로 분리된 경우 첫 번째 저자만
    author = author.split(",")[0].strip()
    return author


def fetch_all_books(sb):
    """DB에서 전체 도서 로드"""
    print("도서 데이터 로드 중...")
    books = []
    offset = 0
    page_size = 1000
    columns = (
        "id, isbn, title, author, publisher, description, "
        "rich_description, enriched_description, sales_point, "
        "page_count, mood_tags, library_keywords, dominant_colors, "
        "genre, cover_url, created_at"
    )

    while True:
        result = sb.table("books").select(columns).range(offset, offset + page_size - 1).execute()
        if not result.data:
            break
        books.extend(result.data)
        if len(result.data) < page_size:
            break
        offset += page_size

    print(f"  {len(books)}권 로드 완료\n")
    return books


def find_duplicate_groups(books: list) -> list:
    """
    중복 그룹 식별

    로직:
      1. 제목 정규화 + 부제 제거 → 핵심 제목 추출
      2. (핵심 제목, 정규화 저자) 기준으로 그룹핑
      3. 2권 이상인 그룹만 반환
      4. 시리즈물 그룹은 제외
    """
    # 그룹핑: (정규화 제목, 정규화 저자) → [books]
    groups = defaultdict(list)

    for book in books:
        norm_title = normalize_title(book["title"])
        core_title = strip_subtitle(norm_title)
        norm_author = normalize_author(book.get("author", ""))

        # 제목이 너무 짧으면 스킵 (오탐 방지)
        if len(core_title) < 2:
            continue

        key = (core_title, norm_author)
        groups[key].append(book)

    # 2권 이상인 그룹만 필터
    dup_groups = []
    series_excluded = 0

    for key, group_books in groups.items():
        if len(group_books) < 2:
            continue

        # 시리즈물 제외
        if is_series_item(group_books):
            series_excluded += 1
            continue

        dup_groups.append({
            "core_title": key[0],
            "author": key[1],
            "books": group_books,
        })

    # 그룹 크기 내림차순 정렬
    dup_groups.sort(key=lambda g: len(g["books"]), reverse=True)

    print(f"중복 그룹: {len(dup_groups)}개 (시리즈 제외: {series_excluded}개)")
    return dup_groups


def select_canonical(group_books: list) -> tuple:
    """
    정본 선정 — 풍부도 점수 기준

    Returns: (canonical_book, non_canonical_books)
    """
    scored = [(book, compute_richness_score(book)) for book in group_books]
    scored.sort(key=lambda x: x[1], reverse=True)

    canonical = scored[0][0]
    non_canonical = [book for book, _ in scored[1:]]

    return canonical, non_canonical


def print_group_detail(group: dict, canonical: dict, non_canonical: list, verbose: bool = False):
    """그룹 상세 출력"""
    print(f"\n  [{group['core_title']}] — {group['author']} ({len(group['books'])}권)")

    for book in group["books"]:
        score = compute_richness_score(book)
        marker = " << 정본" if book["id"] == canonical["id"] else " (중복)"
        print(f"    {'*' if book['id'] == canonical['id'] else '-'} {book['title'][:50]}")
        print(f"      ISBN: {book.get('isbn', '?')}  |  점수: {score:.0f}{marker}")

        if verbose:
            desc_len = len(book.get("description") or "")
            rich = "O" if book.get("rich_description") else "X"
            sp = book.get("sales_point") or 0
            print(f"      desc: {desc_len}자  rich: {rich}  sales: {sp}")


def apply_dedup(sb, non_canonical_books: list, canonical_id: str):
    """
    DB에 중복 마킹 적용

    non-canonical 도서에 canonical_book_id 설정
    """
    for book in non_canonical_books:
        try:
            sb.table("books").update({
                "canonical_book_id": canonical_id
            }).eq("id", book["id"]).execute()
        except Exception as e:
            print(f"    오류: {book['title'][:30]} — {e}")


def ensure_column_exists():
    """canonical_book_id 컬럼이 없으면 안내"""
    # Supabase Python 클라이언트로는 DDL을 직접 실행할 수 없음
    # SQL Editor에서 아래를 실행해야 함
    print("=" * 60)
    print("사전 준비: canonical_book_id 컬럼이 필요합니다.")
    print("Supabase SQL Editor에서 아래 SQL을 실행해주세요:\n")
    print("  ALTER TABLE books")
    print("  ADD COLUMN IF NOT EXISTS canonical_book_id UUID")
    print("  REFERENCES books(id);")
    print()
    print("  COMMENT ON COLUMN books.canonical_book_id IS")
    print("  '중복 도서의 정본 ID. NULL이면 정본이거나 중복 아님';")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="도서 중복 제거 (소프트 머지)")
    parser.add_argument("--apply", action="store_true", help="실제 DB 반영 (기본: 미리보기)")
    parser.add_argument("--yes", "-y", action="store_true", help="확인 프롬프트 없이 바로 적용 (CI용)")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    args = parser.parse_args()

    sb = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    )

    mode = "적용 모드" if args.apply else "미리보기 모드 (--apply로 실제 반영)"
    print("=" * 60)
    print(f"도서 중복 제거 — {mode}")
    print("=" * 60)

    # 1. 전체 도서 로드
    books = fetch_all_books(sb)

    # 2. 중복 그룹 식별
    dup_groups = find_duplicate_groups(books)

    if not dup_groups:
        print("\n중복 도서가 없습니다!")
        return

    # 3. 그룹별 정본 선정 + 리포트
    total_duplicates = 0
    merge_plan = []  # (canonical, non_canonical_list)

    for group in dup_groups:
        canonical, non_canonical = select_canonical(group["books"])
        print_group_detail(group, canonical, non_canonical, verbose=args.verbose)
        merge_plan.append((canonical, non_canonical))
        total_duplicates += len(non_canonical)

    # 4. 요약
    print(f"\n{'=' * 60}")
    print(f"요약:")
    print(f"  중복 그룹: {len(dup_groups)}개")
    print(f"  중복 도서: {total_duplicates}권 (canonical_book_id 마킹 대상)")
    print(f"  정본 도서: {len(dup_groups)}권 (유지)")
    print(f"{'=' * 60}")

    # 5. 적용
    if args.apply:
        print("\nDB에 반영 중...")
        ensure_column_exists()

        if not args.yes:
            confirm = input("\n계속하시겠습니까? (y/N): ")
            if confirm.lower() != "y":
                print("취소됨")
                return

        applied = 0
        for canonical, non_canonical in merge_plan:
            apply_dedup(sb, non_canonical, canonical["id"])
            applied += len(non_canonical)
            print(f"  {canonical['title'][:30]} — {len(non_canonical)}권 마킹 완료")

        print(f"\n완료! {applied}권에 canonical_book_id 설정됨")
    else:
        print(f"\n위 {total_duplicates}건을 반영하려면: python3 scripts/dedup_books.py --apply")
        print("\n주의: --apply 실행 전에 canonical_book_id 컬럼을 추가해야 합니다.")
        ensure_column_exists()


if __name__ == "__main__":
    main()
