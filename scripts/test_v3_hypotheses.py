"""v3 추천 로직 가설 비교 — 7개 가설 × 5 페르소나 자동 평가.

지표:
1. Genre Coverage: Top 20에 등장한 유저가 읽은 L1 비율
2. Genre HHI: Top 20 L1 분포 Herfindahl 지수 (낮을수록 다양)
3. Author Hit Rate: 유저가 좋아한 책 저자가 Top 20 등장 비율
4. Dislike Avoidance: 싫어한 L1 회피 (해당 페르소나만)

출력: scripts/test_data/hypothesis_results_<timestamp>.md
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


def compute_components(index, candidate_id, good_ids, bad_ids, fb_data):
    cand = index.get_book(candidate_id)
    if cand is None:
        return None
    good_books = [index.get_book(bid) for bid in good_ids if index.get_book(bid)]
    bad_books = [index.get_book(bid) for bid in bad_ids if index.get_book(bid)]

    desc_score = max(float(np.dot(b.desc, cand.desc)) for b in good_books) if good_books else 0.0
    if bad_books:
        desc_score -= max(float(np.dot(b.desc, cand.desc)) for b in bad_books)

    l1_score = max(float(np.dot(b.l1, cand.l1)) for b in good_books) if good_books else 0.0
    l2_score = max(float(np.dot(b.l2, cand.l2)) for b in good_books) if good_books else 0.0

    reason_sims = []
    for b in good_books:
        if b.reasons and cand.reasons:
            reason_sims.append(maxsim(b.reasons, cand.reasons))
    for b in bad_books:
        if b.reasons and cand.reasons:
            reason_sims.append(-maxsim(b.reasons, cand.reasons))
    reason_score = float(np.mean(reason_sims)) if reason_sims else 0.0

    fb_vals = []
    for bid, fb in fb_data.items():
        sign = -1.0 if fb["is_dislike"] else 1.0
        fb_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_desc_score = float(np.mean(fb_vals)) if fb_vals else 0.0

    return {"desc": desc_score, "l1": l1_score, "l2": l2_score,
            "reason": reason_score, "fb_desc": fb_desc_score}


def score_all(index, persona, fb_data, weights):
    """가중치로 모든 후보 스코어링."""
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
        scores[cid] = sum(weights.get(k, 0) * v for k, v in comp.items())
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ─── 후처리 전략 ───

def take_top(scored, top_n=TOP_N):
    return scored[:top_n]


def cap_static(scored, meta, top_n=TOP_N, max_per_l1=6):
    selected, l1_count = [], Counter()
    for bid, score in scored:
        l1 = parse_l1(meta.get(bid, {}).get("genre", ""))
        if l1_count[l1] >= max_per_l1:
            continue
        selected.append((bid, score))
        l1_count[l1] += 1
        if len(selected) >= top_n:
            break
    return selected


def cap_dynamic(scored, meta, persona, top_n=TOP_N):
    """유저가 읽은 L1 분포 비율을 그대로 추천 비율에 반영."""
    # 1. 유저가 읽은 L1 분포 (good만)
    user_l1_books = []
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        # candidate meta에서 가져오기 어려우니, 간접: persona 내 책 ID로 meta 조회
        bid = b["book_id"]
        m = meta.get(bid)
        if m:
            user_l1_books.append(parse_l1(m.get("genre", "")))

    if not user_l1_books:
        return scored[:top_n]

    user_dist = Counter(user_l1_books)
    total = sum(user_dist.values())
    # 각 L1의 max quota = 비율 × top_n (반올림, 최소 1)
    quotas = {}
    for l1, cnt in user_dist.items():
        q = max(1, round(cnt / total * top_n))
        quotas[l1] = q

    # 미등장 L1을 위한 여분 슬롯 (top_n 초과분 보정)
    selected, l1_count = [], Counter()
    for bid, score in scored:
        l1 = parse_l1(meta.get(bid, {}).get("genre", ""))
        # 유저가 읽지 않은 L1은 잔여 슬롯으로만 채움
        if l1 in quotas:
            if l1_count[l1] >= quotas[l1]:
                continue
        else:
            if sum(l1_count.values()) >= top_n:
                continue
        selected.append((bid, score))
        l1_count[l1] += 1
        if len(selected) >= top_n:
            break
    return selected


def mmr_rerank(scored, index, top_n=TOP_N, lambda_div=0.6, pool=100):
    candidates = list(scored[:pool])
    selected, selected_descs = [], []
    while candidates and len(selected) < top_n:
        best_idx, best_mmr = None, -1e9
        for i, (bid, rel) in enumerate(candidates):
            book = index.get_book(bid)
            if book is None:
                continue
            if not selected_descs:
                mmr = lambda_div * rel
            else:
                max_sim = max(float(np.dot(book.desc, sd)) for sd in selected_descs)
                mmr = lambda_div * rel - (1 - lambda_div) * max_sim * 5
            if mmr > best_mmr:
                best_mmr, best_idx = mmr, i
        if best_idx is None:
            break
        bid, rel = candidates.pop(best_idx)
        selected.append((bid, rel))
        b = index.get_book(bid)
        if b is not None:
            selected_descs.append(b.desc)
    return selected


# ─── 가설 정의 ───

HYPOTHESES = [
    ("H1_baseline", "현재 v3 그대로",
     dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0),
     "raw"),
    ("H2_weights_tuned", "L1↓ desc↑",
     dict(reason=1.5, desc=2.0, l1=1.0, l2=0.5, fb_desc=2.0),
     "raw"),
    ("H3_cap_static", "현재 + L1 cap=6",
     dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0),
     "cap_static"),
    ("H4_cap_dynamic", "현재 + 유저 분포 비례 cap",
     dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0),
     "cap_dynamic"),
    ("H5_mmr", "현재 + MMR rerank",
     dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0),
     "mmr"),
    ("H6_combined", "tuned + dynamic cap",
     dict(reason=1.5, desc=2.0, l1=1.0, l2=0.5, fb_desc=2.0),
     "cap_dynamic"),
    ("H7_desc_first", "desc 강화 + dynamic cap + reason 보존",
     dict(reason=2.0, desc=3.0, l1=0.5, l2=0.3, fb_desc=2.0),
     "cap_dynamic"),
]


def apply_postprocess(scored, mode, index, meta, persona):
    if mode == "raw":
        return take_top(scored)
    if mode == "cap_static":
        return cap_static(scored, meta)
    if mode == "cap_dynamic":
        return cap_dynamic(scored, meta, persona)
    if mode == "mmr":
        return mmr_rerank(scored, index)
    return take_top(scored)


# ─── 평가 지표 ───

def hhi(items):
    """Herfindahl–Hirschman Index. 0~1, 낮을수록 다양."""
    if not items:
        return 0.0
    counts = Counter(items)
    total = sum(counts.values())
    return sum((c / total) ** 2 for c in counts.values())


def evaluate(top, persona, meta, books_meta_in_index):
    """4개 지표 계산."""
    top_ids = [bid for bid, _ in top]
    top_l1s = [parse_l1(meta.get(bid, {}).get("genre", "")) for bid in top_ids]

    # 1. Genre Coverage
    user_l1s = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m:
            user_l1s.add(parse_l1(m.get("genre", "")))
    if user_l1s:
        present = user_l1s & set(top_l1s)
        coverage = len(present) / len(user_l1s)
    else:
        coverage = 0.0

    # 2. HHI (낮을수록 좋음)
    h = hhi(top_l1s)
    diversity = 1 - h  # 변환: 높을수록 좋음

    # 3. Author Hit Rate
    user_authors = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m and m.get("author"):
            user_authors.add(m["author"].split(",")[0].strip())
    top_authors_hit = 0
    for bid in top_ids:
        m = meta.get(bid)
        if not m or not m.get("author"):
            continue
        a = m["author"].split(",")[0].strip()
        if a in user_authors:
            top_authors_hit += 1
    author_hit = top_authors_hit / len(top_ids) if top_ids else 0.0

    # 4. Dislike avoidance
    bad_l1s = set()
    for b in persona["books"]:
        if b["rating"] != "bad":
            continue
        m = meta.get(b["book_id"])
        if m:
            bad_l1s.add(parse_l1(m.get("genre", "")))
    if bad_l1s:
        bad_count = sum(1 for l in top_l1s if l in bad_l1s)
        avoidance = 1.0 - (bad_count / len(top_l1s))
    else:
        avoidance = None  # N/A

    return {
        "coverage": coverage,
        "diversity": diversity,
        "author_hit": author_hit,
        "avoidance": avoidance,
        "top_l1_dist": Counter(top_l1s),
    }


# ─── 메인 ───

def main():
    print("=== v3 가설 비교 ===\n")

    print("[1] 인덱스 로드")
    index, books_in_index, _ = load_index(INDEX_PATH)

    print("[2] 페르소나 + 리뷰 임베딩")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)
    all_reviews = [b["review_text"] for p in data["personas"] for b in p["books"] if b.get("review_text")]
    review_embs = call_embedding(all_reviews) if all_reviews else []
    review_emb_map = dict(zip(all_reviews, review_embs))

    sb = create_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")))

    print("[3] 메타 사전 로드")
    all_book_ids = list(index.book_ids)
    meta = {}
    for i in range(0, len(all_book_ids), 200):
        chunk = all_book_ids[i:i + 200]
        res = sb.table("books").select("id, title, author, genre").in_("id", chunk).execute()
        for r in res.data:
            meta[r["id"]] = r
        time.sleep(0.5)
    print(f"  meta: {len(meta)}")

    output = ["# v3 추천 로직 가설 비교 결과", f"\n생성: {datetime.now().isoformat()}\n"]
    output.append("## 가설 정의\n")
    output.append("| ID | 설명 | 가중치 | 후처리 |")
    output.append("|---|---|---|---|")
    for hid, desc, w, mode in HYPOTHESES:
        wstr = " ".join(f"{k}:{v}" for k, v in w.items())
        output.append(f"| {hid} | {desc} | {wstr} | {mode} |")

    # 각 페르소나마다 전체 가설 실행
    all_results = {}  # {hid: [persona_metric, ...]}
    for hid, _, _, _ in HYPOTHESES:
        all_results[hid] = []

    for persona in data["personas"]:
        print(f"\n[4] {persona['name']}")
        output.append(f"\n## {persona['name']}\n")

        # fb_data
        fb_data = {}
        for b in persona["books"]:
            if b.get("review_text"):
                emb = review_emb_map.get(b["review_text"])
                if emb is not None:
                    fb_data[b["book_id"]] = {
                        "emb": normalize(emb),
                        "is_dislike": b["rating"] == "bad",
                    }

        # 각 unique weight set은 한 번만 스코어링
        weight_cache = {}
        persona_metrics = []
        for hid, hdesc, weights, mode in HYPOTHESES:
            wkey = json.dumps(weights, sort_keys=True)
            if wkey not in weight_cache:
                print(f"  scoring {hid}...")
                weight_cache[wkey] = score_all(index, persona, fb_data, weights)
            scored = weight_cache[wkey]

            top = apply_postprocess(scored, mode, index, meta, persona)
            metrics = evaluate(top, persona, meta, books_in_index)
            persona_metrics.append((hid, metrics, top))
            all_results[hid].append(metrics)

        # 표
        output.append("| 가설 | 커버리지 | 다양성 | 저자hit | 회피 | 분포 |")
        output.append("|---|---|---|---|---|---|")
        for hid, m, top in persona_metrics:
            av = "N/A" if m["avoidance"] is None else f"{m['avoidance']:.2f}"
            dist = ", ".join(f"{l}:{c}" for l, c in m["top_l1_dist"].most_common(3))
            output.append(f"| {hid} | {m['coverage']:.2f} | {m['diversity']:.2f} | "
                           f"{m['author_hit']:.2f} | {av} | {dist} |")

    # 종합
    output.append("\n## 가설별 평균 (5 페르소나)\n")
    output.append("| 가설 | 커버리지 | 다양성 | 저자hit | 회피(하늘) |")
    output.append("|---|---|---|---|---|")
    summary = []
    for hid, _, _, _ in HYPOTHESES:
        ms = all_results[hid]
        avg_cov = np.mean([m["coverage"] for m in ms])
        avg_div = np.mean([m["diversity"] for m in ms])
        avg_auth = np.mean([m["author_hit"] for m in ms])
        avo_vals = [m["avoidance"] for m in ms if m["avoidance"] is not None]
        avg_avo = np.mean(avo_vals) if avo_vals else None
        # 종합 점수: 커버리지×0.4 + 다양성×0.3 + author×0.3
        composite = avg_cov * 0.4 + avg_div * 0.3 + avg_auth * 0.3
        summary.append((hid, avg_cov, avg_div, avg_auth, avg_avo, composite))
        avo_str = "N/A" if avg_avo is None else f"{avg_avo:.2f}"
        output.append(f"| {hid} | {avg_cov:.3f} | {avg_div:.3f} | {avg_auth:.3f} | {avo_str} |")

    output.append("\n## 종합 점수 (커버리지 0.4 + 다양성 0.3 + 저자 0.3)\n")
    output.append("| 순위 | 가설 | 종합 |")
    output.append("|---|---|---|")
    summary.sort(key=lambda x: x[5], reverse=True)
    for rank, (hid, _, _, _, _, comp) in enumerate(summary, 1):
        output.append(f"| {rank} | {hid} | {comp:.3f} |")

    output.append(f"\n**최선: {summary[0][0]}** (종합 {summary[0][5]:.3f})\n")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"hypothesis_results_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output))
    print(f"\n✓ 저장: {out_path}")
    print(f"\n최선: {summary[0][0]} (종합 {summary[0][5]:.3f})")


if __name__ == "__main__":
    main()
