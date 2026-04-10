"""대규모 v3 가설 비교 — 10 페르소나 × 10 가설 × 6 지표.

지표 (각 페르소나 × 가설마다):
1. Coverage      — 유저가 읽은 L1 중 Top 20에 등장한 비율
2. Diversity     — 1 - HHI (Top 20 L1 분포)
3. Author Hit    — 유저가 좋아한 책 저자가 Top 20 등장 비율
4. Top5 Hit      — Top 5 안에서 좋아한 책의 저자/유사 hit
5. Author Div    — Top 20에 등장하는 unique 저자 수 / 20
6. Avoidance     — 싫어한 L1 회피 (해당 페르소나만)

종합 점수: cov×0.25 + div×0.20 + auth×0.20 + top5×0.20 + auth_div×0.15
출력: scripts/test_data/grand_results_<timestamp>.md
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


def first_author(author_str):
    if not author_str:
        return None
    return author_str.split(",")[0].strip()


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


# ─── 후처리 ───

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
    user_l1_books = []
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m:
            user_l1_books.append(parse_l1(m.get("genre", "")))
    if not user_l1_books:
        return scored[:top_n]
    user_dist = Counter(user_l1_books)
    total = sum(user_dist.values())
    quotas = {l1: max(1, round(cnt / total * top_n)) for l1, cnt in user_dist.items()}

    selected, l1_count = [], Counter()
    for bid, score in scored:
        l1 = parse_l1(meta.get(bid, {}).get("genre", ""))
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


def cap_static_strict(scored, meta, top_n=TOP_N):
    return cap_static(scored, meta, top_n=top_n, max_per_l1=4)


# ─── 가설 10개 ───

HYPOTHESES = [
    ("H1_baseline", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0), "raw"),
    ("H2_weights_tuned", dict(reason=1.5, desc=2.0, l1=1.0, l2=0.5, fb_desc=2.0), "raw"),
    ("H3_cap_static_6", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0), "cap6"),
    ("H4_cap_dynamic", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0), "dynamic"),
    ("H5_mmr", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0), "mmr"),
    ("H6_combined", dict(reason=1.5, desc=2.0, l1=1.0, l2=0.5, fb_desc=2.0), "dynamic"),
    ("H7_desc_first", dict(reason=2.0, desc=3.0, l1=0.5, l2=0.3, fb_desc=2.0), "dynamic"),
    ("H8_cap_strict_4", dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0), "cap4"),
    ("H9_reason_max", dict(reason=3.0, desc=2.0, l1=0.5, l2=0.3, fb_desc=2.0), "dynamic"),
    ("H10_no_l1", dict(reason=2.0, desc=3.0, l1=0.0, l2=0.0, fb_desc=2.0), "dynamic"),
    ("H11_auto", dict(reason=2.0, desc=3.0, l1=0.5, l2=0.3, fb_desc=2.0), "auto"),
    ("H12_h7_mild", dict(reason=1.5, desc=2.5, l1=1.0, l2=0.5, fb_desc=2.0), "dynamic"),
]


def cap_auto(scored, meta, persona, top_n=TOP_N):
    """유저 상태에 따라 자동 전략 선택.
    - good 권수 < 4: raw (데이터 부족, 강제 분산 X)
    - unique L1 == 1: raw (마니아, 분산 X)
    - 그 외: dynamic cap
    """
    good_books = [b for b in persona["books"] if b["rating"] == "good"]
    if len(good_books) < 4:
        return take_top(scored, top_n)
    user_l1s = set()
    for b in good_books:
        m = meta.get(b["book_id"])
        if m:
            user_l1s.add(parse_l1(m.get("genre", "")))
    if len(user_l1s) <= 1:
        return take_top(scored, top_n)
    return cap_dynamic(scored, meta, persona, top_n)


def apply_post(scored, mode, index, meta, persona):
    if mode == "raw":
        return take_top(scored)
    if mode == "cap6":
        return cap_static(scored, meta, max_per_l1=6)
    if mode == "cap4":
        return cap_static_strict(scored, meta)
    if mode == "dynamic":
        return cap_dynamic(scored, meta, persona)
    if mode == "mmr":
        return mmr_rerank(scored, index)
    if mode == "auto":
        return cap_auto(scored, meta, persona)
    return take_top(scored)


# ─── 평가 지표 ───

def hhi(items):
    if not items:
        return 0.0
    counts = Counter(items)
    total = sum(counts.values())
    return sum((c / total) ** 2 for c in counts.values())


def evaluate(top, persona, meta):
    top_ids = [bid for bid, _ in top]
    top_l1s = [parse_l1(meta.get(bid, {}).get("genre", "")) for bid in top_ids]

    # Coverage
    user_l1s = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m:
            user_l1s.add(parse_l1(m.get("genre", "")))
    coverage = len(user_l1s & set(top_l1s)) / len(user_l1s) if user_l1s else 0.0

    # Diversity
    diversity = 1 - hhi(top_l1s)

    # Author Hit (Top 20)
    user_authors = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m and m.get("author"):
            a = first_author(m["author"])
            if a:
                user_authors.add(a)
    auth_hit = 0
    for bid in top_ids:
        m = meta.get(bid)
        if m and m.get("author") and first_author(m["author"]) in user_authors:
            auth_hit += 1
    author_hit = auth_hit / len(top_ids) if top_ids else 0.0

    # Top 5 Hit (저자 hit, 더 엄격한 quality)
    top5_hit = 0
    for bid, _ in top[:5]:
        m = meta.get(bid)
        if m and m.get("author") and first_author(m["author"]) in user_authors:
            top5_hit += 1
    top5 = top5_hit / min(5, len(top))

    # Author Diversity (Top 20에 unique 저자 수 / 20)
    top_authors = set()
    for bid in top_ids:
        m = meta.get(bid)
        if m and m.get("author"):
            a = first_author(m["author"])
            if a:
                top_authors.add(a)
    author_div = len(top_authors) / len(top_ids) if top_ids else 0.0

    # Avoidance
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
        avoidance = None

    return {
        "coverage": coverage, "diversity": diversity,
        "author_hit": author_hit, "top5": top5, "author_div": author_div,
        "avoidance": avoidance, "top_l1": Counter(top_l1s),
    }


def main():
    print("=== v3 대규모 가설 비교 ===\n")

    print("[1] 인덱스 로드")
    index, _, _ = load_index(INDEX_PATH)
    print(f"  books: {len(index.book_ids)}")

    print("[2] 페르소나 + 리뷰 임베딩")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)
    print(f"  personas: {len(data['personas'])}")
    all_reviews = list({b["review_text"] for p in data["personas"]
                         for b in p["books"] if b.get("review_text")})
    review_embs = call_embedding(all_reviews) if all_reviews else []
    review_emb_map = dict(zip(all_reviews, review_embs))
    print(f"  reviews: {len(all_reviews)}")

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

    output = ["# v3 대규모 가설 비교 결과", f"\n생성: {datetime.now().isoformat()}",
               f"\n페르소나: {len(data['personas'])}, 가설: {len(HYPOTHESES)}\n"]

    output.append("## 가설 정의\n")
    output.append("| ID | 가중치 | 후처리 |")
    output.append("|---|---|---|")
    for hid, w, mode in HYPOTHESES:
        wstr = " ".join(f"{k}:{v}" for k, v in w.items())
        output.append(f"| {hid} | {wstr} | {mode} |")

    all_results = {hid: [] for hid, _, _ in HYPOTHESES}

    for persona in data["personas"]:
        print(f"\n[4] {persona['name']} ({len(persona['books'])}권)")
        output.append(f"\n## {persona['name']}\n")
        output.append(f"_{persona['profile']}_\n")

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

        weight_cache = {}
        rows = []
        for hid, weights, mode in HYPOTHESES:
            wkey = json.dumps(weights, sort_keys=True)
            if wkey not in weight_cache:
                t0 = time.time()
                weight_cache[wkey] = score_all(index, persona, fb_data, weights)
                print(f"  scored {hid} ({time.time()-t0:.0f}s)")
            scored = weight_cache[wkey]
            top = apply_post(scored, mode, index, meta, persona)
            m = evaluate(top, persona, meta)
            all_results[hid].append(m)
            rows.append((hid, m))

        output.append("| 가설 | Cov | Div | Auth | Top5 | AuthDiv | Avoid | 분포 |")
        output.append("|---|---|---|---|---|---|---|---|")
        for hid, m in rows:
            av = "N/A" if m["avoidance"] is None else f"{m['avoidance']:.2f}"
            dist = ", ".join(f"{l}:{c}" for l, c in m["top_l1"].most_common(3))
            output.append(f"| {hid} | {m['coverage']:.2f} | {m['diversity']:.2f} | "
                           f"{m['author_hit']:.2f} | {m['top5']:.2f} | {m['author_div']:.2f} | "
                           f"{av} | {dist} |")

    # 종합
    output.append("\n## 가설별 평균 (10 페르소나)\n")
    output.append("| 가설 | Cov | Div | Auth | Top5 | AuthDiv | Avoid | Composite |")
    output.append("|---|---|---|---|---|---|---|---|")
    summary = []
    for hid, _, _ in HYPOTHESES:
        ms = all_results[hid]
        avg_cov = float(np.mean([m["coverage"] for m in ms]))
        avg_div = float(np.mean([m["diversity"] for m in ms]))
        avg_auth = float(np.mean([m["author_hit"] for m in ms]))
        avg_t5 = float(np.mean([m["top5"] for m in ms]))
        avg_ad = float(np.mean([m["author_div"] for m in ms]))
        av_vals = [m["avoidance"] for m in ms if m["avoidance"] is not None]
        avg_av = float(np.mean(av_vals)) if av_vals else None
        comp = avg_cov * 0.25 + avg_div * 0.20 + avg_auth * 0.20 + avg_t5 * 0.20 + avg_ad * 0.15
        summary.append((hid, avg_cov, avg_div, avg_auth, avg_t5, avg_ad, avg_av, comp))
        av_str = "N/A" if avg_av is None else f"{avg_av:.2f}"
        output.append(f"| {hid} | {avg_cov:.3f} | {avg_div:.3f} | {avg_auth:.3f} | "
                       f"{avg_t5:.3f} | {avg_ad:.3f} | {av_str} | **{comp:.3f}** |")

    output.append("\n## 종합 순위\n")
    output.append("| 순위 | 가설 | Composite |")
    output.append("|---|---|---|")
    summary.sort(key=lambda x: x[7], reverse=True)
    for rank, row in enumerate(summary, 1):
        output.append(f"| {rank} | {row[0]} | {row[7]:.3f} |")
    output.append(f"\n**최선: {summary[0][0]}** (composite {summary[0][7]:.3f})")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"grand_results_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output))
    print(f"\n✓ 저장: {out_path}")
    print(f"\n최선: {summary[0][0]} (composite {summary[0][7]:.3f})")
    return out_path


if __name__ == "__main__":
    main()
