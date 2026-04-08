"""H10_no_l1 다각도 검증 — 4가지 테스트.

A. 랜덤 50 페르소나 스트레스 — H1 vs H10
B. 18 페르소나 실제 Top 10 책 시각 출력
C. 안정성 — 1권 swap 시 Top 20 overlap
D. 적대적 — 이상 입력 (빈, all bad, dup) 처리

출력: scripts/test_data/h10_validation_<timestamp>.md
"""
import os, sys, json, time, random
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

H1_WEIGHTS = dict(reason=1.0, desc=0.5, l1=3.0, l2=1.0, fb_desc=2.0)
H10_WEIGHTS = dict(reason=2.0, desc=3.0, l1=0.0, l2=0.0, fb_desc=2.0)


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


def first_author(s):
    return s.split(",")[0].strip() if s else None


def maxsim(qs, cs):
    if not qs or not cs:
        return 0.0
    q = np.stack([np.array(v, dtype=np.float32) for v in qs])
    c = np.stack([np.array(v, dtype=np.float32) for v in cs])
    return float((q @ c.T).max(axis=1).mean())


def compute_components(index, cid, good_ids, bad_ids, fb_data):
    cand = index.get_book(cid)
    if cand is None:
        return None
    good_books = [index.get_book(b) for b in good_ids if index.get_book(b)]
    bad_books = [index.get_book(b) for b in bad_ids if index.get_book(b)]

    desc_score = max(float(np.dot(b.desc, cand.desc)) for b in good_books) if good_books else 0.0
    if bad_books:
        desc_score -= max(float(np.dot(b.desc, cand.desc)) for b in bad_books)
    l1_score = max(float(np.dot(b.l1, cand.l1)) for b in good_books) if good_books else 0.0
    l2_score = max(float(np.dot(b.l2, cand.l2)) for b in good_books) if good_books else 0.0

    rs = []
    for b in good_books:
        if b.reasons and cand.reasons:
            rs.append(maxsim(b.reasons, cand.reasons))
    for b in bad_books:
        if b.reasons and cand.reasons:
            rs.append(-maxsim(b.reasons, cand.reasons))
    reason_score = float(np.mean(rs)) if rs else 0.0

    fbs = []
    for bid, fb in fb_data.items():
        sign = -1.0 if fb["is_dislike"] else 1.0
        fbs.append(sign * float(np.dot(fb["emb"], cand.desc)))
    fb_score = float(np.mean(fbs)) if fbs else 0.0

    return dict(desc=desc_score, l1=l1_score, l2=l2_score,
                reason=reason_score, fb_desc=fb_score)


def score_persona(index, persona, fb_data, weights):
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


def evaluate_top(top, persona, meta):
    top_ids = [bid for bid, _ in top]
    top_l1s = [parse_l1(meta.get(bid, {}).get("genre", "")) for bid in top_ids]

    user_l1s = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m:
            user_l1s.add(parse_l1(m.get("genre", "")))
    coverage = len(user_l1s & set(top_l1s)) / len(user_l1s) if user_l1s else 0.0

    counts = Counter(top_l1s)
    total = sum(counts.values())
    diversity = 1 - sum((c / total) ** 2 for c in counts.values()) if total else 0.0

    user_authors = set()
    for b in persona["books"]:
        if b["rating"] != "good":
            continue
        m = meta.get(b["book_id"])
        if m and m.get("author"):
            a = first_author(m["author"])
            if a:
                user_authors.add(a)
    auth_hit = sum(1 for bid in top_ids
                    if meta.get(bid, {}).get("author") and
                    first_author(meta[bid]["author"]) in user_authors)
    author_hit = auth_hit / len(top_ids) if top_ids else 0.0

    bad_l1s = set()
    for b in persona["books"]:
        if b["rating"] != "bad":
            continue
        m = meta.get(b["book_id"])
        if m:
            bad_l1s.add(parse_l1(m.get("genre", "")))
    avoidance = (1.0 - sum(1 for l in top_l1s if l in bad_l1s) / len(top_l1s)) if bad_l1s else None

    return dict(coverage=coverage, diversity=diversity, author_hit=author_hit,
                avoidance=avoidance, top_l1=Counter(top_l1s))


def run_h10(index, persona, fb_data, meta):
    scored = score_persona(index, persona, fb_data, H10_WEIGHTS)
    return cap_dynamic(scored, meta, persona)


def run_h1(index, persona, fb_data):
    scored = score_persona(index, persona, fb_data, H1_WEIGHTS)
    return scored[:TOP_N]


def main():
    print("=== H10 다각도 검증 ===\n")

    print("[setup] 인덱스 로드")
    index, _, _ = load_index(INDEX_PATH)

    print("[setup] 페르소나 로드")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)
    personas = data["personas"]

    print("[setup] 리뷰 임베딩")
    all_reviews = list({b["review_text"] for p in personas for b in p["books"]
                         if b.get("review_text")})
    review_embs = call_embedding(all_reviews) if all_reviews else []
    review_emb_map = dict(zip(all_reviews, review_embs))

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    print("[setup] 메타 로드")
    all_book_ids = list(index.book_ids)
    meta = {}
    for i in range(0, len(all_book_ids), 200):
        chunk = all_book_ids[i:i + 200]
        res = sb.table("books").select("id, title, author, genre").in_("id", chunk).execute()
        for r in res.data:
            meta[r["id"]] = r
        time.sleep(0.5)
    print(f"  meta: {len(meta)}")

    out = ["# H10 다각도 검증 결과", f"\n생성: {datetime.now().isoformat()}\n"]

    def fb_for(persona):
        d = {}
        for b in persona["books"]:
            if b.get("review_text"):
                emb = review_emb_map.get(b["review_text"])
                if emb is not None:
                    d[b["book_id"]] = dict(emb=normalize(emb), is_dislike=b["rating"] == "bad")
        return d

    # ─── Test B: 실제 Top 10 출력 ───
    print("\n[Test B] 18 페르소나 실제 Top 10")
    out.append("\n## Test B — 18 페르소나 H10 실제 Top 10\n")
    for p in personas:
        print(f"  {p['name']}...")
        fb = fb_for(p)
        top = run_h10(index, p, fb, meta)
        h1_top = run_h1(index, p, fb)
        m_h10 = evaluate_top(top, p, meta)
        m_h1 = evaluate_top(h1_top, p, meta)
        out.append(f"\n### {p['name']} — {p['profile']}\n")
        good = sum(1 for b in p['books'] if b['rating'] == 'good')
        bad = sum(1 for b in p['books'] if b['rating'] == 'bad')
        out.append(f"_입력: good {good} / bad {bad}_\n")
        out.append(f"H1 — Cov {m_h1['coverage']:.2f} Div {m_h1['diversity']:.2f} Auth {m_h1['author_hit']:.2f}")
        out.append(f"H10 — Cov {m_h10['coverage']:.2f} Div {m_h10['diversity']:.2f} Auth {m_h10['author_hit']:.2f}\n")
        out.append("**H10 Top 10:**")
        for i, (bid, score) in enumerate(top[:10], 1):
            mm = meta.get(bid, {})
            l1 = parse_l1(mm.get("genre", ""))
            out.append(f"{i}. ({score:.2f}) [{l1}] {mm.get('title','?')[:55]} — {(mm.get('author','') or '')[:25]}")
        out.append(f"\nH10 분포: {dict(m_h10['top_l1'].most_common())}")

    # ─── Test A: 50 랜덤 페르소나 ───
    print("\n[Test A] 50 랜덤 페르소나 스트레스")
    out.append("\n\n## Test A — 50 랜덤 페르소나 (H1 vs H10)\n")

    # 책 풀: index에 있는 모든 책
    all_ids = list(index.book_ids)
    random.seed(123)

    h1_metrics, h10_metrics = [], []
    print("  생성 + 평가 중...")
    for i in range(50):
        # 랜덤 사이즈 (3~25), 랜덤 dislike 비율 (0~30%)
        n_total = random.randint(3, 25)
        bad_ratio = random.choice([0, 0, 0, 0.1, 0.2, 0.3])
        n_bad = int(n_total * bad_ratio)
        n_good = n_total - n_bad
        sample_ids = random.sample(all_ids, n_total)
        books = [{"book_id": bid, "title": meta.get(bid, {}).get("title", "?"),
                  "rating": "good"} for bid in sample_ids[:n_good]]
        books += [{"book_id": bid, "title": meta.get(bid, {}).get("title", "?"),
                   "rating": "bad"} for bid in sample_ids[n_good:]]
        rp = {"id": f"rand_{i}", "name": f"R{i}", "profile": f"random {n_good}g/{n_bad}b", "books": books}

        h1_top = run_h1(index, rp, {})
        h10_top = run_h10(index, rp, {}, meta)
        h1_metrics.append(evaluate_top(h1_top, rp, meta))
        h10_metrics.append(evaluate_top(h10_top, rp, meta))

    def avg(ms, k):
        vals = [m[k] for m in ms if m[k] is not None]
        return float(np.mean(vals)) if vals else 0.0

    out.append("| 지표 | H1 baseline | H10_no_l1 | 개선 |")
    out.append("|---|---|---|---|")
    for k in ["coverage", "diversity", "author_hit"]:
        h1v, h10v = avg(h1_metrics, k), avg(h10_metrics, k)
        delta = h10v - h1v
        out.append(f"| {k} | {h1v:.3f} | {h10v:.3f} | {delta:+.3f} |")

    # 분포 비교: H10이 H1보다 나쁜 건수
    worse_count = sum(1 for h1m, h10m in zip(h1_metrics, h10_metrics)
                       if (h10m["coverage"] + h10m["diversity"] + h10m["author_hit"]) <
                          (h1m["coverage"] + h1m["diversity"] + h1m["author_hit"]))
    out.append(f"\n**H10이 H1보다 나쁜 케이스: {worse_count}/50** ({worse_count*2}%)")

    # ─── Test C: 안정성 (1권 swap) ───
    print("\n[Test C] 안정성 테스트")
    out.append("\n\n## Test C — 안정성 (1권 변경 시 Top 20 overlap)\n")

    test_personas = [p for p in personas if p["name"] in ["서연", "민호", "지은", "준혁", "태원"]]
    out.append("| 페르소나 | 원래 Top 20 → swap 후 Top 20 overlap |")
    out.append("|---|---|")
    for p in test_personas:
        fb = fb_for(p)
        original_top = set(bid for bid, _ in run_h10(index, p, fb, meta))

        # 첫 번째 good 책을 다른 같은 장르 책으로 교체
        good_books = [b for b in p["books"] if b["rating"] == "good"]
        if not good_books:
            continue
        original_book = good_books[0]
        original_l1 = parse_l1(meta.get(original_book["book_id"], {}).get("genre", ""))

        # 같은 L1 다른 책 찾기
        candidates = [bid for bid in all_ids
                       if parse_l1(meta.get(bid, {}).get("genre", "")) == original_l1
                       and bid not in [b["book_id"] for b in p["books"]]]
        if not candidates:
            continue
        new_bid = random.choice(candidates)

        # 새 페르소나 (1권 swap)
        new_books = [dict(b) for b in p["books"]]
        new_books[0] = {"book_id": new_bid, "title": meta.get(new_bid, {}).get("title", "?"),
                         "rating": "good"}
        new_p = {"id": p["id"] + "_swap", "name": p["name"] + "_swap",
                  "profile": "", "books": new_books}
        new_top = set(bid for bid, _ in run_h10(index, new_p, fb_for(new_p), meta))

        overlap = len(original_top & new_top) / len(original_top)
        out.append(f"| {p['name']} | {overlap:.2f} ({int(overlap*20)}/20 동일) |")

    # ─── Test D: 적대적 ───
    print("\n[Test D] 적대적 테스트")
    out.append("\n\n## Test D — 적대적 입력\n")

    # D1: 모든 책이 같은 L2
    sample_l2_ids = []
    sample_l2 = None
    for bid in all_ids[:300]:
        m = meta.get(bid, {})
        if m.get("genre"):
            parts = m["genre"].split(">")
            if len(parts) >= 3:
                if sample_l2 is None:
                    sample_l2 = parts[2].strip()
                if parts[2].strip() == sample_l2:
                    sample_l2_ids.append(bid)
                    if len(sample_l2_ids) >= 5:
                        break

    out.append("\n### D1: 같은 L2 5권만 (극단 마니아)\n")
    if len(sample_l2_ids) >= 5:
        adv1 = {"id": "adv1", "name": "adv1", "profile": "same L2",
                 "books": [{"book_id": bid, "title": meta.get(bid, {}).get("title"), "rating": "good"}
                           for bid in sample_l2_ids[:5]]}
        top = run_h10(index, adv1, {}, meta)
        m = evaluate_top(top, adv1, meta)
        out.append(f"L2: {sample_l2}")
        out.append(f"Cov {m['coverage']:.2f} / Div {m['diversity']:.2f} / 분포 {dict(m['top_l1'].most_common(3))}")
        out.append("Top 5:")
        for bid, s in top[:5]:
            mm = meta.get(bid, {})
            out.append(f"  - ({s:.2f}) {mm.get('title','?')[:50]}")

    # D2: 모두 bad (좋아하는 거 없음)
    out.append("\n### D2: 모두 bad (싫은 책 5권만)\n")
    adv2_ids = random.sample(all_ids, 5)
    adv2 = {"id": "adv2", "name": "adv2", "profile": "all bad",
             "books": [{"book_id": bid, "title": meta.get(bid, {}).get("title"), "rating": "bad"}
                       for bid in adv2_ids]}
    try:
        top = run_h10(index, adv2, {}, meta)
        m = evaluate_top(top, adv2, meta)
        out.append(f"결과 권수: {len(top)}, 분포: {dict(m['top_l1'].most_common(3))}")
    except Exception as e:
        out.append(f"에러: {e}")

    # D3: 좋아요 1권만
    out.append("\n### D3: 좋아요 1권만\n")
    adv3 = {"id": "adv3", "name": "adv3", "profile": "1 good",
             "books": [{"book_id": all_ids[0], "title": meta.get(all_ids[0], {}).get("title"), "rating": "good"}]}
    top = run_h10(index, adv3, {}, meta)
    m = evaluate_top(top, adv3, meta)
    out.append(f"입력: {meta.get(all_ids[0], {}).get('title', '?')}")
    out.append(f"Cov {m['coverage']:.2f} / Div {m['diversity']:.2f} / 분포 {dict(m['top_l1'].most_common(3))}")
    out.append("Top 5:")
    for bid, s in top[:5]:
        mm = meta.get(bid, {})
        out.append(f"  - ({s:.2f}) [{parse_l1(mm.get('genre',''))}] {mm.get('title','?')[:50]}")

    # 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"h10_validation_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"\n✓ 저장: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
