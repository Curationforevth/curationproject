"""
books 테이블 제목 정제 스크립트
- 괄호 안 부가 정보 제거: (영화 특별판), (양장), (더블특전판) 등
- 대시 뒤 부가 설명 제거: " - SL Comics, 띠지 + ..." 등
- 대시 뒤 부제는 유지: " - 제172회 아쿠타가와상 수상작" 등

사용법: python3 scripts/clean_book_titles.py
  --dry-run  : 변경 사항만 미리보기 (기본값)
  --apply    : 실제로 DB에 반영
"""

import os
import re
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

# 제거할 괄호 안 패턴 — 모든 에디션/판본/굿즈/보너스 정보
REMOVE_PAREN_PATTERNS = [
    r"\(.*?(?:특별판|에디션|한정판|리커버|양장|무선|문고판|기념|보너스|수록|클래식|개정판|완결|특전).*?\)",
    r"\(리딩\)",
    r"\(리스닝\)",
    r"\(보카\)",
]

# 대시 뒤에서 제거할 패턴 (굿즈/구성품 설명)
REMOVE_DASH_PATTERNS = [
    r"\s*-\s*SL Comic.*$",
    r"\s*-\s*S코믹스.*$",
    r"\s*-\s*전\d+권.*$",
    r"\s*-\s*.*(?:카드|스탠드|소책자|북마크|띠지|포스터|포토|스티커|티켓|엽서|pp|수록|독점|강의|MP3|PDF|해설|기출|시험 대비).*$",
    r"\s*\+앱.*$",
    r",\s*완결\s*$",
]


def clean_title(title):
    """제목 정제"""
    cleaned = title

    # 괄호 안 부가 정보 제거
    for pattern in REMOVE_PAREN_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    # 대시 뒤 굿즈/구성품 설명 제거
    for pattern in REMOVE_DASH_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    # 앞뒤 공백, 연속 공백 정리
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def main():
    apply = "--apply" in sys.argv
    mode = "적용 모드" if apply else "미리보기 모드 (--apply로 실제 반영)"

    print(f"📚 제목 정제 — {mode}")
    print("=" * 60)

    # 전체 책 가져오기
    res = supabase.table("books").select("id, title").execute()
    books = res.data

    changes = []
    for book in books:
        original = book["title"]
        cleaned = clean_title(original)

        if original != cleaned:
            changes.append({
                "id": book["id"],
                "original": original,
                "cleaned": cleaned,
            })

    if not changes:
        print("변경할 제목이 없어요!")
        return

    print(f"\n변경 대상: {len(changes)}권\n")

    for i, c in enumerate(changes, 1):
        print(f"  {i}. {c['original']}")
        print(f"     → {c['cleaned']}")
        print()

    if apply:
        print("DB에 반영 중...")
        for c in changes:
            supabase.table("books").update(
                {"title": c["cleaned"]}
            ).eq("id", c["id"]).execute()
            print(f"  ✓ {c['cleaned'][:40]}")

        print(f"\n완료! {len(changes)}권 제목 정제됨")
    else:
        print(f"위 {len(changes)}건을 반영하려면: python3 scripts/clean_book_titles.py --apply")


if __name__ == "__main__":
    main()
