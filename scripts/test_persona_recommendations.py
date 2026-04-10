"""페르소나 5명 기반 v3 추천 품질 테스트.

각 페르소나의 읽은 책 + 피드백을 사용해 Top 20 추천을 뽑고,
장르 분포 / 컴포넌트 스코어 / 싫어요 회피 등 지표를 함께 출력한다.

사용법:
  python3 scripts/test_persona_recommendations.py

출력:
  scripts/test_data/results_<timestamp>.md
"""
import os
import sys
import json
import time
from datetime import datetime
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recommendation-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import numpy as np
from supabase import create_client

from engine.loader import load_index
from engine.scorer import recommend_scores, _score_one
from scripts.lib.openai_helpers import call_embedding

INDEX_PATH = os.path.join(os.path.dirname(__file__), "..",
                           "recommendation-server", "data", "index.pkl")
PERSONAS_PATH = os.path.join(os.path.dirname(__file__), "test_data", "personas.json")
TOP_N = 20


def normalize(v):
    a = np.array(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


def build_fb_data(books, embeddings_by_review):
    """review_text가 있는 책만 fb_data에 추가."""
    fb_data = {}
    for b in books:
        review = b.get("review_text")
        if not review:
            continue
        emb = embeddings_by_review.get(review)
        if emb is None:
            continue
        fb_data[b["book_id"]] = {
            "emb": normalize(emb),
            "is_dislike": b["rating"] == "bad",
        }
    return fb_data


def build_liked_books(books):
    return {b["book_id"]: {"rating": b["rating"]} for b in books}


def fetch_book_metadata(sb, book_ids):
    """추천 결과 책 메타데이터 단일 쿼리 조회."""
    if not book_ids:
        return {}
    # chunks of 200 to avoid URL length
    meta = {}
    for i in range(0, len(book_ids), 200):
        chunk = book_ids[i:i + 200]
        res = sb.table("books").select("id, title, author, genre").in_("id", chunk).execute()
        for r in res.data:
            meta[r["id"]] = r
        time.sleep(0.5)
    return meta


def parse_l1(genre_str):
    """genre 문자열에서 L1만 추출."""
    if not genre_str:
        return "?"
    parts = [p.strip() for p in genre_str.split(">")]
    if parts and parts[0] in ("국내도서", "외국도서", "eBook"):
        parts = parts[1:]
    return parts[0] if parts else "?"


def evaluate_persona(index, persona, sb):
    print(f"\n{'='*70}")
    print(f"페르소나: {persona['name']} — {persona['profile']}")
    print(f"{'='*70}")

    books = persona["books"]
    print(f"읽은 책: {len(books)}권 (good: {sum(1 for b in books if b['rating']=='good')}, "
          f"bad: {sum(1 for b in books if b['rating']=='bad')})")

    # 1. review_text 임베딩 일괄 생성
    reviews = [b["review_text"] for b in books if b.get("review_text")]
    print(f"피드백 리뷰: {len(reviews)}개 → 임베딩 중...")
    emb_map = {}
    if reviews:
        batch_embs = call_embedding(reviews)
        for review, emb in zip(reviews, batch_embs):
            emb_map[review] = emb

    # 2. 입력 구성
    liked_books = build_liked_books(books)
    fb_data = build_fb_data(books, emb_map)
    print(f"fb_data entries: {len(fb_data)}")

    # 3. 추천
    print("추천 계산 중...")
    t0 = time.time()
    scores = recommend_scores(index, liked_books, fb_data)
    print(f"  {len(scores)}권 scoring 완료 ({time.time()-t0:.1f}초)")

    # 4. Top N
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    top_ids = [bid for bid, _ in top]

    # 5. 메타 조회
    meta = fetch_book_metadata(sb, top_ids)

    # 6. 컴포넌트별 스코어 (샘플 5건만)
    top_5 = top[:5]
    breakdown_rows = []
    for bid, score in top_5:
        cand = index.get_book(bid)
        if cand is None:
            continue
        good_ids = [b["book_id"] for b in books if b["rating"] == "good"]
        bad_ids = [b["book_id"] for b in books if b["rating"] == "bad"]

        # 각 컴포넌트 독립 계산
        good_descs = [index.get_book(i).desc for i in good_ids if index.get_book(i)]
        good_l1s = [index.get_book(i).l1 for i in good_ids if index.get_book(i)]
        good_l2s = [index.get_book(i).l2 for i in good_ids if index.get_book(i)]

        desc_s = max(float(np.dot(d, cand.desc)) for d in good_descs) if good_descs else 0.0
        l1_s = max(float(np.dot(l, cand.l1)) for l in good_l1s) if good_l1s else 0.0
        l2_s = max(float(np.dot(l, cand.l2)) for l in good_l2s) if good_l2s else 0.0
        breakdown_rows.append((bid, score, desc_s, l1_s, l2_s))

    # 7. 장르 분포
    l1_counts = Counter()
    for bid, _ in top:
        g = meta.get(bid, {}).get("genre", "")
        l1_counts[parse_l1(g)] += 1

    # 결과 문자열 구성
    out = []
    out.append(f"\n## 페르소나: {persona['name']}")
    out.append(f"\n**프로필**: {persona['profile']}")
    out.append(f"**읽은 책**: {len(books)}권 (good {sum(1 for b in books if b['rating']=='good')} / "
               f"bad {sum(1 for b in books if b['rating']=='bad')})")
    out.append(f"**상세 피드백**: {len(fb_data)}개")

    out.append(f"\n### 읽은 책 목록")
    for b in books:
        tag = "❤️" if b["rating"] == "good" else ("💔" if b["rating"] == "bad" else "")
        fb_mark = " 📝" if b.get("review_text") else ""
        out.append(f"- {tag}{fb_mark} {b['title']}")

    out.append(f"\n### Top {TOP_N} 추천")
    out.append("| # | 점수 | 장르 | 제목 |")
    out.append("|---|------|------|------|")
    for i, (bid, score) in enumerate(top, 1):
        m = meta.get(bid, {})
        l1 = parse_l1(m.get("genre", ""))
        title = m.get("title", bid[:8])[:60]
        out.append(f"| {i} | {score:.4f} | {l1} | {title} |")

    out.append(f"\n### 장르 분포 (Top 20)")
    for l1, cnt in l1_counts.most_common():
        pct = cnt / len(top) * 100
        out.append(f"- {l1}: {cnt}권 ({pct:.0f}%)")

    out.append(f"\n### 컴포넌트 스코어 (Top 5)")
    out.append("| 제목 | total | desc | L1 | L2 |")
    out.append("|------|-------|------|-----|-----|")
    for bid, total, d, l1, l2 in breakdown_rows:
        t = meta.get(bid, {}).get("title", bid[:8])[:40]
        out.append(f"| {t} | {total:.3f} | {d:.3f} | {l1:.3f} | {l2:.3f} |")

    # 하늘(싫어요 포함 페르소나) 전용: bad 책 카테고리가 Top 20에 없는지 확인
    if any(b["rating"] == "bad" for b in books):
        bad_l1s = set()
        for b in books:
            if b["rating"] == "bad":
                bres = sb.table("books").select("genre").eq("id", b["book_id"]).limit(1).execute()
                if bres.data:
                    bad_l1s.add(parse_l1(bres.data[0].get("genre", "")))
        overlap = sum(1 for bid, _ in top if parse_l1(meta.get(bid, {}).get("genre", "")) in bad_l1s)
        out.append(f"\n### 싫어요 회피")
        out.append(f"- 싫어한 장르 (L1): {', '.join(bad_l1s)}")
        out.append(f"- Top 20 중 싫어한 장르 개수: **{overlap}권**")

    return "\n".join(out)


def main():
    print("=== v3 추천 품질 테스트 ===")
    print(f"\n[1] 인덱스 로드: {INDEX_PATH}")
    index, books_meta, built_at = load_index(INDEX_PATH)
    print(f"  books: {len(index.book_ids)}, built: {built_at}")

    print(f"\n[2] 페르소나 로드: {PERSONAS_PATH}")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)
    print(f"  페르소나 수: {len(data['personas'])}")

    sb = create_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")))

    # 인덱스에 있는 책만 사용 (누락 경고)
    for p in data["personas"]:
        missing = [b["title"] for b in p["books"] if index.get_book(b["book_id"]) is None]
        if missing:
            print(f"  ⚠ {p['name']}: 인덱스에 없는 책 {len(missing)}개 — {missing}")

    print(f"\n[3] 페르소나별 평가 시작\n")
    all_outputs = ["# v3 추천 품질 테스트 결과", f"\n생성: {datetime.now().isoformat()}",
                    f"인덱스: {built_at}, books: {len(index.book_ids)}"]

    for persona in data["personas"]:
        out = evaluate_persona(index, persona, sb)
        all_outputs.append(out)
        all_outputs.append("\n---")

    # 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"results_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_outputs))
    print(f"\n✓ 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
