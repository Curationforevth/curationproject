"""
추천 신뢰도 임계값 실험 스크립트

권수 × 피드백 깊이 조합별로 랜덤 책을 선택하고,
각 책에 대한 구체적 피드백 예시와 함께 추천 결과를 출력.
Eden이 직접 추천 품질을 판단하여 임계값을 결정.

사용법:
  python3 scripts/experiment_confidence.py
  python3 scripts/experiment_confidence.py --seed 123   # 다른 랜덤 시드
"""

import argparse
import json
import os
import random
import sys

import numpy as np
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

EMOTION_TAGS = ['잔잔한', '따뜻한', '긴장감', '몰입', '여운', '유쾌한', '무거운', '서정적', '속도감', '생각할거리']
RATINGS = ['good', 'good', 'good', 'neutral', 'bad']  # good 편향 (현실적)

REVIEW_TEMPLATES = [
    "캐릭터들이 정말 살아있는 느낌이었다. 특히 주인공의 내면 묘사가 인상적.",
    "문체가 아름다워서 밑줄 치면서 읽었다. 한 문장 한 문장이 시 같았다.",
    "몰입감이 장난 아니었다. 새벽까지 손에서 놓을 수가 없었다.",
    "생각할 거리가 많은 책. 다 읽고 나서도 한참 여운이 남았다.",
    "기대보다 별로였다. 전개가 너무 느려서 중간에 지루했다.",
    "세계관이 독특해서 재밌었다. 이런 상상력이 부럽다.",
    "가볍게 읽기 좋았다. 스트레스 풀릴 때 딱인 책.",
    "저자의 경험이 진솔하게 느껴졌다. 나도 비슷한 경험이 있어서 공감됐다.",
    "논리적이고 체계적인 구성. 실생활에 바로 적용할 수 있는 내용이 많았다.",
    "분위기가 너무 좋았다. 읽는 내내 그 세계에 빠져 있는 느낌.",
]


def parse_emb(emb_str):
    if isinstance(emb_str, str):
        return np.array(json.loads(emb_str))
    return np.array(emb_str)


def generate_feedback(depth_level):
    """피드백 깊이별 예시 생성.

    depth_level:
      1 = 읽음만
      2 = 호오만
      3 = 호오 + 태그 1~2개
      4 = 호오 + 태그 3개+
      5 = 호오 + 태그 + 리뷰
    """
    fb = {"rating": None, "emotion_tags": [], "review_text": None}

    if depth_level >= 2:
        fb["rating"] = random.choice(RATINGS)

    if depth_level >= 3:
        n_tags = random.randint(1, 2)
        fb["emotion_tags"] = random.sample(EMOTION_TAGS, n_tags)

    if depth_level >= 4:
        n_tags = random.randint(3, 5)
        fb["emotion_tags"] = random.sample(EMOTION_TAGS, n_tags)

    if depth_level >= 5:
        fb["review_text"] = random.choice(REVIEW_TEMPLATES)

    return fb


def feedback_weight(fb):
    if fb.get("rating") == "bad":
        return 0
    review = fb.get("review_text") or ""
    tags = fb.get("emotion_tags") or []
    rating = fb.get("rating")
    w = 1.0
    if review and len(review) >= 50:
        w = 3.0
    elif tags and len(tags) >= 1:
        w = 2.0
    elif rating:
        w = 1.5
    return w


def depth_score(fb):
    review = fb.get("review_text") or ""
    tags = fb.get("emotion_tags") or []
    rating = fb.get("rating")
    if review and len(review) >= 50:
        return 5
    if tags and len(tags) >= 3:
        return 4
    if tags and len(tags) >= 1:
        return 3
    if rating:
        return 2
    return 1


def confidence_score(feedbacks, genres):
    total_depth = sum(depth_score(fb) for fb in feedbacks)
    n_books = len(feedbacks)
    n_genres = len(genres)
    div = min(n_genres / 4.0, 1.0) if n_books >= 3 else 0

    ratings = set(fb["rating"] for fb in feedbacks if fb.get("rating"))
    rating_var = len(ratings) / 3.0 if ratings else 0

    score = min(
        (total_depth / 25.0) * 0.4 +
        div * 0.3 +
        rating_var * 0.15 +
        min(n_books / 10.0, 1.0) * 0.15,
        1.0
    )
    return score


def fmt_feedback(fb):
    parts = []
    if fb["rating"]:
        emoji = {"good": "👍", "neutral": "😐", "bad": "👎"}
        parts.append(emoji.get(fb["rating"], fb["rating"]))
    if fb["emotion_tags"]:
        parts.append(f"태그: {', '.join(fb['emotion_tags'])}")
    if fb["review_text"]:
        parts.append(f'리뷰: "{fb["review_text"][:40]}..."')
    if not parts:
        parts.append("(읽음만)")
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

    # 데이터 로드
    print("데이터 로딩 중...")
    emb_map = {}
    offset = 0
    while True:
        r = sb.table('book_embeddings').select('book_id, embedding').range(offset, offset + 999).execute()
        if not r.data:
            break
        for row in r.data:
            emb_map[row['book_id']] = parse_emb(row['embedding'])
        if len(r.data) < 1000:
            break
        offset += 1000

    meta_map = {}
    offset = 0
    while True:
        r = sb.table('books').select('id, title, author, genre').range(offset, offset + 999).execute()
        if not r.data:
            break
        for row in r.data:
            meta_map[row['id']] = row
        if len(r.data) < 1000:
            break
        offset += 1000

    book_ids = list(emb_map.keys())
    print(f"  {len(book_ids)}권 로드 완료\n")

    def fmt_book(bid):
        m = meta_map.get(bid, {})
        t = m.get('title', '?')[:35]
        g = m.get('genre', '') or ''
        gs = g.split('>')[-1][:15] if '>' in g else g[:15]
        return f"{t} [{gs}]"

    def recommend(taste_vec, exclude, n=5):
        results = []
        tn = np.linalg.norm(taste_vec)
        for bid, emb in emb_map.items():
            if bid in exclude:
                continue
            sim = float(np.dot(taste_vec, emb) / (tn * np.linalg.norm(emb)))
            results.append((bid, sim))
        results.sort(key=lambda x: -x[1])
        return results[:n]

    # 실험 케이스
    cases = [
        {"name": "1권, 읽음만", "n": 1, "depth": 1},
        {"name": "1권, 풀피드백", "n": 1, "depth": 5},
        {"name": "3권, 읽음만", "n": 3, "depth": 1},
        {"name": "3권, 호오+태그", "n": 3, "depth": 3},
        {"name": "3권, 풀피드백", "n": 3, "depth": 5},
        {"name": "5권, 읽음만", "n": 5, "depth": 1},
        {"name": "5권, 호오+태그 소수", "n": 5, "depth": 3},
        {"name": "5권, 풀피드백", "n": 5, "depth": 5},
        {"name": "5권, 혼합 (읽음1+태그2+풀피드백2)", "n": 5, "depth": "mixed"},
        {"name": "10권, 읽음만", "n": 10, "depth": 1},
        {"name": "10권, 혼합 (읽음3+태그4+풀피드백3)", "n": 10, "depth": "mixed"},
    ]

    for case in cases:
        print(f"\n{'=' * 70}")
        print(f"📚 {case['name']}")
        print(f"{'=' * 70}")

        selected = random.sample(book_ids, case["n"])

        # 피드백 생성
        feedbacks = []
        if case["depth"] == "mixed":
            n = case["n"]
            if n == 5:
                depths = [1, 3, 3, 5, 5]
            elif n == 10:
                depths = [1, 1, 1, 3, 3, 3, 3, 5, 5, 5]
            else:
                depths = [3] * n
            random.shuffle(depths)
            for d in depths:
                feedbacks.append(generate_feedback(d))
        else:
            for _ in range(case["n"]):
                feedbacks.append(generate_feedback(case["depth"]))

        # 장르 수집
        genres = set()
        for bid in selected:
            g = (meta_map.get(bid, {}).get('genre') or '')
            if g:
                top = g.split('>')[1] if '>' in g else g
                genres.add(top)

        conf = confidence_score(feedbacks, genres)

        print(f"\n  confidence: {conf:.3f} | 장르 {len(genres)}개")
        print(f"\n  ── 입력 ──")
        for bid, fb in zip(selected, feedbacks):
            w = feedback_weight(fb)
            ds = depth_score(fb)
            print(f"    {fmt_book(bid)}")
            print(f"      {fmt_feedback(fb)} (weight={w}, depth={ds})")

        # 가중 평균
        vecs, ws = [], []
        for bid, fb in zip(selected, feedbacks):
            w = feedback_weight(fb)
            if w > 0 and bid in emb_map:
                vecs.append(emb_map[bid])
                ws.append(w)

        if not vecs:
            print("\n  ⚠ 유효한 벡터 없음 (전부 bad?)")
            continue

        taste = np.average(np.array(vecs), axis=0, weights=np.array(ws))
        recs = recommend(taste, set(selected), n=5)

        print(f"\n  ── 추천 Top-5 ──")
        for bid, sim in recs:
            print(f"    {sim:.4f} — {fmt_book(bid)}")

    print(f"\n{'=' * 70}")
    print("실험 완료. 다른 시드로 다시 돌리려면: --seed N")


if __name__ == "__main__":
    main()
