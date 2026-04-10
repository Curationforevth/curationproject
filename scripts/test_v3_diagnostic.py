"""v3 추천 엔진 전수 진단 + 가중치 grid search + MMR rerank 비교.

목적:
1. L1/L2 cosine 분포 측정 (포화 가설 검증)
2. 컴포넌트 단독 추천 (어떤 신호가 효과적인지)
3. 가중치 grid search (8개 조합 비교)
4. MMR diversity rerank 효과 측정

출력: scripts/test_data/diagnostic_<timestamp>.md
"""
import os
import sys
import json
import time
from datetime import datetime
from collections import Counter
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recommendation-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import numpy as np
from supabase import create_client

from engine.loader import load_index
from scripts.lib.openai_helpers import call_embedding

INDEX_PATH = os.path.join(os.path.dirname(__file__), "..",
                           "recommendation-server", "data", "index.pkl")
PERSONAS_PATH = os.path.join(os.path.dirname(__file__), "test_data", "personas.json")
TOP_N = 20


# ─── 유틸 ───

def normalize(v):
    a = np.array(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


def parse_l1(genre_str):
    if not genre_str:
        return "?"
    parts = [p.strip() for p in genre_str.split(">")]
    if parts and parts[0] in ("국내도서", "외국도서", "eBook"):
        parts = parts[1:]
    return parts[0] if parts else "?"


def maxsim(query_vecs, candidate_vecs):
    if not query_vecs or not candidate_vecs:
        return 0.0
    q = np.stack([np.array(v, dtype=np.float32) for v in query_vecs])
    c = np.stack([np.array(v, dtype=np.float32) for v in candidate_vecs])
    sims = q @ c.T
    return float(sims.max(axis=1).mean())


# ─── 컴포넌트별 raw 점수 계산 ───

def compute_components(index, candidate_id, good_ids, bad_ids, fb_data):
    """단일 후보의 모든 컴포넌트 raw 점수 반환."""
    cand = index.get_book(candidate_id)
    if cand is None:
        return None

    good_books = [index.get_book(bid) for bid in good_ids if index.get_book(bid)]
    bad_books = [index.get_book(bid) for bid in bad_ids if index.get_book(bid)]

    # desc
    if good_books:
        desc_score = max(float(np.dot(b.desc, cand.desc)) for b in good_books)
    else:
        desc_score = 0.0
    if bad_books:
        desc_score -= max(float(np.dot(b.desc, cand.desc)) for b in bad_books)

    # L1
    if good_books:
        l1_score = max(float(np.dot(b.l1, cand.l1)) for b in good_books)
    else:
        l1_score = 0.0

    # L2
    if good_books:
        l2_score = max(float(np.dot(b.l2, cand.l2)) for b in good_books)
    else:
        l2_score = 0.0

    # reason (단순 maxsim 평균)
    reason_sims = []
    for b in good_books:
        if b.reasons and cand.reasons:
            reason_sims.append(maxsim(b.reasons, cand.reasons))
    for b in bad_books:
        if b.reasons and cand.reasons:
            reason_sims.append(-maxsim(b.reasons, cand.reasons))
    reason_score = float(np.mean(reason_sims)) if reason_sims else 0.0

    # fb_desc
    fb_vals = []
    for bid, fb in fb_data.items():
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_desc_score = float(np.mean(fb_vals)) if fb_vals else 0.0

    return {
        "desc": desc_score,
        "l1": l1_score,
        "l2": l2_score,
        "reason": reason_score,
        "fb_desc": fb_desc_score,
    }


# ─── 1. 포화 진단 ───

def diagnose_saturation(index, persona, fb_data, sb):
    """L1/L2 cosine 분포 측정 — 포화 가설 검증."""
    good_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "good"]
    bad_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "bad"]

    l1_dist, l2_dist, desc_dist = [], [], []
    for cid in index.book_ids:
        if cid in good_ids or cid in bad_ids:
            continue
        comp = compute_components(index, cid, good_ids, bad_ids, fb_data)
        if comp:
            l1_dist.append(comp["l1"])
            l2_dist.append(comp["l2"])
            desc_dist.append(comp["desc"])

    def stats(arr, name):
        a = np.array(arr)
        # 분포 bins
        bins = [0, 0.3, 0.5, 0.7, 0.9, 0.99, 1.001]
        labels = ["<0.3", "0.3~0.5", "0.5~0.7", "0.7~0.9", "0.9~0.99", "==1.0"]
        hist, _ = np.histogram(a, bins=bins)
        return {
            "name": name,
            "min": float(a.min()),
            "max": float(a.max()),
            "mean": float(a.mean()),
            "std": float(a.std()),
            "hist": dict(zip(labels, hist.tolist())),
            "saturation_pct": float((a >= 0.99).sum() / len(a) * 100),
        }

    return [stats(l1_dist, "L1"), stats(l2_dist, "L2"), stats(desc_dist, "desc")]


# ─── 2. 추천 계산 (가중치 조합 가능) ───

def recommend_with_weights(index, persona, fb_data, weights, top_n=TOP_N):
    """가중치 조합으로 추천. weights={'desc':0.5, 'l1':3.0, ...}"""
    good_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "good"]
    bad_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "bad"]
    read = set(good_ids + bad_ids)

    scores = {}
    for cid in index.book_ids:
        if cid in read:
            continue
        comp = compute_components(index, cid, good_ids, bad_ids, fb_data)
        if comp is None:
            continue
        score = sum(weights.get(k, 0) * v for k, v in comp.items())
        scores[cid] = score

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]


# ─── 3. MMR diversity rerank ───

def mmr_rerank(index, scored, lambda_div=0.7, top_n=TOP_N):
    """MMR: 다양성을 고려한 재랭킹.
    score = λ * relevance - (1-λ) * max_sim_to_selected
    """
    candidates = list(scored)
    selected = []
    selected_descs = []

    while candidates and len(selected) < top_n:
        best_idx = None
        best_mmr = -1e9
        for i, (bid, rel) in enumerate(candidates):
            book = index.get_book(bid)
            if book is None:
                continue
            if not selected_descs:
                mmr = lambda_div * rel
            else:
                max_sim = max(float(np.dot(book.desc, sd)) for sd in selected_descs)
                mmr = lambda_div * rel - (1 - lambda_div) * max_sim * 5  # scale
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        if best_idx is None:
            break
        bid, rel = candidates.pop(best_idx)
        selected.append((bid, rel))
        b = index.get_book(bid)
        if b is not None:
            selected_descs.append(b.desc)

    return selected


# ─── 4. L1 cap (다양성 강제) ───

def diversity_cap(scored, meta, max_per_l1=8, top_n=TOP_N):
    """동일 L1이 max_per_l1을 넘지 못하게 강제."""
    selected = []
    l1_count = Counter()
    for bid, score in scored:
        l1 = parse_l1(meta.get(bid, {}).get("genre", ""))
        if l1_count[l1] >= max_per_l1:
            continue
        selected.append((bid, score))
        l1_count[l1] += 1
        if len(selected) >= top_n:
            break
    return selected


# ─── 메인 ───

def main():
    print("=== v3 진단 + 로직 비교 테스트 ===\n")

    print("[1] 인덱스 로드")
    index, books_meta, _ = load_index(INDEX_PATH)
    print(f"  books: {len(index.book_ids)}")

    print("[2] 페르소나 로드")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)

    sb = create_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")))

    # 모든 페르소나의 review_text 임베딩 일괄 생성 (1회)
    print("[3] 리뷰 임베딩 일괄 생성")
    all_reviews = []
    for p in data["personas"]:
        for b in p["books"]:
            if b.get("review_text"):
                all_reviews.append(b["review_text"])
    print(f"  reviews: {len(all_reviews)}")
    if all_reviews:
        embs = call_embedding(all_reviews)
        review_emb_map = dict(zip(all_reviews, embs))
    else:
        review_emb_map = {}

    # 가중치 조합 (8개)
    weight_configs = [
        ("baseline (현재)", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0)),
        ("L1 약화", dict(reason=1.0, desc=1.5, l1=1.0, l2=1.0, fb_desc=2.0)),
        ("desc 강화", dict(reason=1.0, desc=2.5, l1=1.0, l2=1.0, fb_desc=2.0)),
        ("reason 중심", dict(reason=3.0, desc=1.0, l1=0.5, l2=0.5, fb_desc=2.0)),
        ("L1 무력화", dict(reason=2.0, desc=2.0, l1=0.0, l2=0.5, fb_desc=2.0)),
        ("균등", dict(reason=1.0, desc=1.0, l1=1.0, l2=1.0, fb_desc=1.0)),
        ("desc 단독 강화", dict(reason=0.5, desc=3.0, l1=0.5, l2=0.5, fb_desc=2.0)),
        ("fb 의존", dict(reason=0.5, desc=0.5, l1=0.5, l2=0.5, fb_desc=4.0)),
    ]

    # 컴포넌트 단독 모드
    component_modes = [
        ("desc only", dict(desc=1.0)),
        ("L1 only", dict(l1=1.0)),
        ("L2 only", dict(l2=1.0)),
        ("reason only", dict(reason=1.0)),
        ("fb only", dict(fb_desc=1.0)),
    ]

    output = ["# v3 추천 엔진 진단 리포트", f"\n생성: {datetime.now().isoformat()}\n"]

    # 메타 사전 로드 (전체 v3 책)
    print("[4] 책 메타 사전 로드")
    all_book_ids = list(index.book_ids)
    meta = {}
    for i in range(0, len(all_book_ids), 200):
        chunk = all_book_ids[i:i + 200]
        res = sb.table("books").select("id, title, genre").in_("id", chunk).execute()
        for r in res.data:
            meta[r["id"]] = r
        time.sleep(0.5)
    print(f"  meta loaded: {len(meta)}")

    for persona in data["personas"]:
        print(f"\n[5] 페르소나: {persona['name']}")
        output.append(f"\n## {persona['name']} — {persona['profile']}\n")

        # fb_data 구성
        fb_data = {}
        for b in persona["books"]:
            if b.get("review_text"):
                emb = review_emb_map.get(b["review_text"])
                if emb is not None:
                    fb_data[b["book_id"]] = {
                        "emb": normalize(emb),
                        "is_dislike": b["rating"] == "bad",
                    }

        good_n = sum(1 for b in persona["books"] if b["rating"] == "good")
        bad_n = sum(1 for b in persona["books"] if b["rating"] == "bad")
        output.append(f"읽은 책: good {good_n} / bad {bad_n}, 피드백 {len(fb_data)}개\n")

        # ── 1. 포화 진단
        print(f"  포화 진단...")
        sat = diagnose_saturation(index, persona, fb_data, sb)
        output.append("\n### 포화 진단 (cosine 분포)\n")
        output.append("| 컴포넌트 | min | max | mean | std | =1.0 비율 |")
        output.append("|---|---|---|---|---|---|")
        for s in sat:
            output.append(f"| {s['name']} | {s['min']:.3f} | {s['max']:.3f} | {s['mean']:.3f} | {s['std']:.3f} | {s['saturation_pct']:.1f}% |")
        output.append("\n분포 히스토그램:\n")
        output.append("| 컴포넌트 | <0.3 | 0.3~0.5 | 0.5~0.7 | 0.7~0.9 | 0.9~0.99 | =1.0 |")
        output.append("|---|---|---|---|---|---|---|")
        for s in sat:
            h = s["hist"]
            output.append(f"| {s['name']} | {h['<0.3']} | {h['0.3~0.5']} | {h['0.5~0.7']} | {h['0.7~0.9']} | {h['0.9~0.99']} | {h['==1.0']} |")

        # ── 2. 컴포넌트 단독 — Top 5만
        print(f"  컴포넌트 단독 추천...")
        output.append("\n### 컴포넌트 단독 Top 5\n")
        for name, w in component_modes:
            top = recommend_with_weights(index, persona, fb_data, w, top_n=5)
            output.append(f"\n**{name}:**")
            for bid, score in top:
                m = meta.get(bid, {})
                l1 = parse_l1(m.get("genre", ""))
                title = m.get("title", bid[:8])[:50]
                output.append(f"- ({score:.3f}) [{l1}] {title}")

        # ── 3. 가중치 grid search — L1 분포만 비교
        print(f"  가중치 grid search...")
        output.append("\n### 가중치 조합별 Top 20 장르 분포\n")
        output.append("| 조합 | 분포 | Top 1 |")
        output.append("|------|------|-------|")

        grid_results = {}
        for name, w in weight_configs:
            top = recommend_with_weights(index, persona, fb_data, w, top_n=TOP_N)
            l1s = Counter()
            for bid, _ in top:
                l1s[parse_l1(meta.get(bid, {}).get("genre", ""))] += 1
            top1 = meta.get(top[0][0], {}).get("title", "?")[:30] if top else "?"
            dist = ", ".join(f"{l}:{c}" for l, c in l1s.most_common(3))
            output.append(f"| {name} | {dist} | {top1} |")
            grid_results[name] = top

        # ── 4. MMR + L1 cap 효과
        print(f"  MMR + cap 효과...")
        baseline_top = grid_results["baseline (현재)"]
        # 더 많이 뽑은 후 rerank
        baseline_50 = recommend_with_weights(index, persona, fb_data, weight_configs[0][1], top_n=100)
        mmr_top = mmr_rerank(index, baseline_50, lambda_div=0.7, top_n=TOP_N)
        cap_top = diversity_cap(baseline_50, meta, max_per_l1=6, top_n=TOP_N)

        output.append("\n### 다양성 처리 비교 (Top 20 L1 분포)\n")
        output.append("| 방식 | L1 분포 |")
        output.append("|------|---------|")
        for label, top in [("baseline", baseline_top), ("MMR rerank (λ=0.7)", mmr_top), ("L1 cap (max 6)", cap_top)]:
            l1s = Counter()
            for bid, _ in top:
                l1s[parse_l1(meta.get(bid, {}).get("genre", ""))] += 1
            dist = ", ".join(f"{l}:{c}" for l, c in l1s.most_common())
            output.append(f"| {label} | {dist} |")

        output.append("\n---")

    # 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"diagnostic_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output))
    print(f"\n✓ 저장: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
