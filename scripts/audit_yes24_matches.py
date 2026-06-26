"""YES24 rich_description 오매칭 audit (1회용, 읽기 전용).

배경: 과거 _find_matching_page 의 fallback 이 '제목 일치 + 저자 성(last_name)
substring' 만으로 매칭해, 같은 제목·같은 성 다른 책의 rich_description 이 잘못
저장됐을 수 있다. rich_description 은 desc 임베딩·취향 매칭에 직결돼 추천 정확도를
오염시킨다. 이 스크립트로 오염 규모를 추정한다.

동작:
  - 기본: rich_description 보유 책의 rich_description_matched_via 분포 출력(싼 점검).
    (과거 저장분은 NULL = 매칭 경로 미상.)
  - --verify --limit N: 샘플 N권을 YES24 에서 ISBN 으로 재검색→현재의 *강화된*
    매칭(author_matches)으로 재판정. 분류:
      isbn          : ISBN 완전일치 발견 (신뢰 높음)
      title_fallback: 제목+저자 강매칭 (현 기준 통과 — 대체로 정상)
      NO_MATCH      : 현 기준으로 매칭 실패인데 rich_description 보유 → **오염 의심**
                      (과거 느슨한 매칭으로만 저장됐을 가능성)

읽기 전용 — DB 쓰기/재임베딩 없음. 오염 확정분의 NULL 처리·재임베딩은 별도 단계.

사용:
  python3 scripts/audit_yes24_matches.py                    # 분포만(외부호출 없음)
  python3 scripts/audit_yes24_matches.py --verify --limit 50  # 50권 샘플 재검증
"""
from __future__ import annotations

import argparse
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from yes24_scraper import Yes24Scraper, is_non_standard_isbn  # noqa: E402


def fetch_rich_books(sb, limit: int | None = None) -> list[dict]:
    """rich_description 보유 책 조회(페이지네이션)."""
    out: list[dict] = []
    offset = 0
    page = 1000
    while True:
        q = (sb.table("books")
             .select("id, isbn, title, author, rich_description_matched_via")
             .not_.is_("rich_description", "null")
             .range(offset, offset + page - 1))
        rows = q.execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
        if limit and len(out) >= limit:
            break
    return out[:limit] if limit else out


def main():
    p = argparse.ArgumentParser(description="YES24 rich_description 오매칭 audit (읽기 전용)")
    p.add_argument("--verify", action="store_true",
                   help="샘플을 YES24 재검색해 현 강화 기준으로 재판정(외부 호출)")
    p.add_argument("--limit", type=int, default=50,
                   help="--verify 시 검증할 샘플 권수 (기본 50)")
    args = p.parse_args()

    scraper = Yes24Scraper(dry_run=True)  # dry_run → 어떤 경로에서도 DB 쓰기 없음
    sb = scraper.sb

    books = fetch_rich_books(sb)
    total = len(books)
    print(f"📚 rich_description 보유: {total}권")

    # 싼 점검: matched_via 분포
    dist: dict = {}
    for b in books:
        v = b.get("rich_description_matched_via") or "NULL(과거·미상)"
        dist[v] = dist.get(v, 0) + 1
    print("  matched_via 분포:")
    for k, v in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")

    if not args.verify:
        print("\n(--verify 미지정 — 외부 재검증 생략. 분포만 출력.)")
        return 0

    # 샘플 재검증
    sample = [b for b in books if b.get("isbn") and not is_non_standard_isbn(b["isbn"])]
    sample = sample[: args.limit]
    print(f"\n🔎 샘플 {len(sample)}권 YES24 재검증 (현 강화 매칭 기준)...\n")

    counts = {"isbn": 0, "title_fallback": 0, "no_match": 0, "search_fail": 0}
    suspects: list[dict] = []

    for i, b in enumerate(sample, 1):
        title, author, isbn = b["title"], b.get("author") or "", b["isbn"]
        try:
            goods_ids = scraper._search_goods_ids(title, author) or \
                scraper._search_goods_ids(title, "")
            if not goods_ids:
                counts["search_fail"] += 1
                time.sleep(scraper.REQUEST_DELAY)
                continue
            time.sleep(scraper.REQUEST_DELAY)
            _html, matched_via = scraper._find_matching_page(
                goods_ids, isbn, expected_title=title, expected_author=author)
            if matched_via == "isbn":
                counts["isbn"] += 1
            elif matched_via == "title_fallback":
                counts["title_fallback"] += 1
            else:
                counts["no_match"] += 1
                suspects.append({"id": b["id"], "isbn": isbn, "title": title})
        except Exception as e:
            counts["search_fail"] += 1
            print(f"  ✗ ({isbn}) {title[:30]}: {e}")
        if i % 10 == 0:
            print(f"  {i}/{len(sample)} | {counts}")
        time.sleep(scraper.REQUEST_DELAY)

    print(f"\n{'=' * 50}\n재검증 결과 (샘플 {len(sample)}):")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    checked = counts["isbn"] + counts["title_fallback"] + counts["no_match"]
    if checked:
        rate = counts["no_match"] / checked * 100
        print(f"\n⚠ 오염 의심(NO_MATCH) 비율: {rate:.1f}% ({counts['no_match']}/{checked})")
    if suspects:
        print("\n오염 의심 책(최대 30):")
        for s in suspects[:30]:
            print(f"  {s['isbn']}  {s['title'][:40]}")
    print("\n(읽기 전용 audit 완료. NULL 처리·재임베딩은 Eden 승인 후 별도 실행.)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
