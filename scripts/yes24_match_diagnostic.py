# scripts/yes24_match_diagnostic.py
"""YES24 매칭 실패 원인 진단.

rich_description이 NULL인 책 300권을 샘플링하여
YES24 매칭 실패 원인을 분류하고 통계를 출력한다.

사용법:
  python3 scripts/yes24_match_diagnostic.py              # 300권 진단
  python3 scripts/yes24_match_diagnostic.py --limit 50   # 50권만
"""
import argparse
import csv
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.yes24_scraper import (
    is_non_standard_isbn, build_search_query,
    extract_isbn_from_html, isbn_matches, clean_section_text,
)

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("requests, beautifulsoup4 필요: pip install requests beautifulsoup4")
    sys.exit(1)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36',
}
REQUEST_DELAY = 1.0
MAX_SEARCH_RESULTS = 5
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "yes24_diagnostic_result.csv")


def fetch_sample_books(sb, limit, non_standard_count=50):
    """stratified 샘플링: 비표준 ISBN + 일반 ISBN."""
    all_books = []
    offset = 0
    while True:
        res = sb.table("books") \
            .select("id, isbn, title, author") \
            .is_("rich_description", "null") \
            .not_.is_("isbn", "null") \
            .order("sales_point", desc=True) \
            .range(offset, offset + 999).execute()
        if not res.data:
            break
        all_books.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    non_standard = [b for b in all_books if is_non_standard_isbn(b["isbn"])]
    standard = [b for b in all_books if not is_non_standard_isbn(b["isbn"])]

    # stratified: 비표준 최대 non_standard_count + 나머지 일반
    sample_ns = non_standard[:min(non_standard_count, len(non_standard))]
    remaining = limit - len(sample_ns)
    sample_std = random.sample(standard, min(remaining, len(standard))) if standard else []

    print(f"  전체 NULL: {len(all_books)}권 (비표준: {len(non_standard)}, 일반: {len(standard)})")
    print(f"  샘플: 비표준 {len(sample_ns)} + 일반 {len(sample_std)} = {len(sample_ns) + len(sample_std)}권")

    return sample_ns + sample_std


def diagnose_book(session, book):
    """단일 책의 YES24 매칭 시도 + 실패 원인 분류."""
    isbn = book["isbn"]
    title = book["title"]
    author = book.get("author", "") or ""

    # 1) 비표준 ISBN
    if is_non_standard_isbn(isbn):
        return "non_standard_isbn", None

    # 2) YES24 검색
    query = build_search_query(title, author)
    try:
        url = f'https://www.yes24.com/Product/Search?domain=BOOK&query={requests.utils.quote(query)}'
        r = session.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        goods_ids = [el.get('data-goods-no') for el in soup.select('[data-goods-no]')[:MAX_SEARCH_RESULTS]]
    except Exception as e:
        return "search_error", str(e)

    if not goods_ids:
        return "not_found", f"query={query}"

    # 3) 상세 페이지 ISBN 매칭
    for i, goods_id in enumerate(goods_ids):
        try:
            r = session.get(f'https://www.yes24.com/Product/Goods/{goods_id}', timeout=10)
            r.raise_for_status()
            html = r.text
        except Exception:
            continue

        page_isbn = extract_isbn_from_html(html)
        if isbn_matches(page_isbn, isbn):
            # 4) 콘텐츠 추출 시도
            soup2 = BeautifulSoup(html, 'html.parser')
            has_content = False
            for sid in ['infoset_introduce', 'infoset_pubReivew', 'infoset_inBook']:
                el = soup2.select_one(f'#{sid}')
                if el:
                    raw = el.get_text(separator='\n', strip=True)
                    cleaned = clean_section_text(raw)
                    if cleaned:
                        has_content = True
                        break
            if has_content:
                return "success", f"goods_id={goods_id}"
            else:
                return "no_content", f"goods_id={goods_id}"

        time.sleep(0.3)

    return "isbn_mismatch", f"goods_ids={goods_ids}, db_isbn={isbn}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    print(f"YES24 매칭 진단 (샘플 {args.limit}권)\n")
    books = fetch_sample_books(sb, args.limit)

    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    for i, book in enumerate(books):
        reason, detail = diagnose_book(session, book)
        results.append({
            "isbn": book["isbn"],
            "title": book["title"][:40],
            "author": (book.get("author") or "")[:20],
            "result": reason,
            "detail": detail or "",
        })

        if (i + 1) % 50 == 0 or i + 1 == len(books):
            print(f"  [{i+1}/{len(books)}] 진행 중...", flush=True)

        time.sleep(REQUEST_DELAY)

    # CSV 저장
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["isbn", "title", "author", "result", "detail"])
        writer.writeheader()
        writer.writerows(results)
    print(f"\n결과 CSV: {OUTPUT_CSV}")

    # 통계 출력
    from collections import Counter
    counts = Counter(r["result"] for r in results)
    total = len(results)

    print(f"\n{'='*50}")
    print(f"YES24 매칭 진단 결과 ({total}권)")
    print(f"{'='*50}")
    for reason in ["success", "not_found", "isbn_mismatch", "non_standard_isbn", "no_content", "search_error"]:
        cnt = counts.get(reason, 0)
        pct = cnt / total * 100 if total else 0
        marker = "⚠" if pct >= 20 else " "
        print(f"  {marker} {reason:20s}: {cnt:4d}건 ({pct:5.1f}%)")
    print(f"{'='*50}")

    # 판단 기준 안내
    print("\n판단 기준:")
    if counts.get("isbn_mismatch", 0) / total >= 0.20:
        print("  → isbn_mismatch ≥ 20%: ISBN 양방향 변환 + fuzzy title 매칭 추가 권장")
    if counts.get("not_found", 0) / total >= 0.20:
        print("  → not_found ≥ 20%: 검색 쿼리 변형 로직 추가 권장")
    if counts.get("non_standard_isbn", 0) / total >= 0.10:
        print("  → non_standard_isbn ≥ 10%: title-only 검색 모드 추가 권장")
    if counts.get("no_content", 0) / total >= 0.10:
        print("  → no_content ≥ 10%: 추가 HTML 섹션 탐색 권장")
    if counts.get("success", 0) / total >= 0.15:
        print("  → success ≥ 15%: 단순 재실행만으로 커버리지 향상 가능")
    if all(counts.get(r, 0) / total < 0.10 for r in ["isbn_mismatch", "not_found", "non_standard_isbn", "no_content"]):
        print("  → 모든 유형 < 10%: 현상 유지 (개선 ROI 낮음)")


if __name__ == "__main__":
    main()
