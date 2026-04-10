"""취향 기반 추천 — 4가지 reason 조합 모드 테스트.

Mode A (engine_full): 원래 엔진 공식 — fb_sim 3.0 + r_sim 0.5
Mode B (fb_primary):  유저 리뷰 극대화 — fb_sim 5.0 + r_sim 0.2
Mode C (filter):      유저 리뷰로 책 reason top-k 필터링
Mode D (pure_user):   리뷰 있으면 책 reason 완전 무시

공통: L1/L2=0, desc=3.0, fb_desc=2.0, cap_dynamic 후처리

신규 지표: Taste Coherence = 추천 책의 reason과 유저 리뷰의 평균 최대 유사도
"""
import os, sys, json, time
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

W_DESC = 3.0
W_FB_DESC = 2.0
W_REASON_BLOCK = 2.0   # reason_score 전체에 곱해지는 외부 가중치


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


def maxsim_mean(qs, cs):
    if not qs or not cs:
        return 0.0
    q = np.stack([np.array(v, dtype=np.float32) for v in qs])
    c = np.stack([np.array(v, dtype=np.float32) for v in cs])
    return float((q @ c.T).max(axis=1).mean())


def max_over_cand(q_vec, cand_reasons):
    """단일 쿼리 벡터 vs 후보 reason들 중 최대 cosine."""
    if not cand_reasons:
        return 0.0
    return max(float(np.dot(q_vec, r)) for r in cand_reasons)


# ─── 4가지 reason 계산 모드 ───

def reason_score_engine_full(index, cand, good_ids, bad_ids, fb_data):
    """Mode A: 엔진 원본 공식."""
    sims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(3.0 * fb_sim + 0.5 * r_sim)
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(1.0 * r_sim)
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-(3.0 * fb_sim + 0.5 * r_sim))
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-1.0 * r_sim)
    return float(np.mean(sims)) if sims else 0.0


def reason_score_fb_primary(index, cand, good_ids, bad_ids, fb_data):
    """Mode B: 유저 리뷰 극대화."""
    sims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(5.0 * fb_sim + 0.2 * r_sim)
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(1.0 * r_sim)
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-(5.0 * fb_sim + 0.2 * r_sim))
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-1.0 * r_sim)
    return float(np.mean(sims)) if sims else 0.0


def _filtered_book_reasons(book_reasons, review_emb, top_k=3):
    """책 reason을 유저 리뷰 유사도로 정렬해 top-k만 반환."""
    if not book_reasons:
        return []
    scored = [(float(np.dot(review_emb, r)), r) for r in book_reasons]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def reason_score_filter(index, cand, good_ids, bad_ids, fb_data):
    """Mode C: 유저 리뷰로 책 reason 필터링 후 매칭."""
    sims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"] and bv.reasons:
            filtered = _filtered_book_reasons(bv.reasons, fb["emb"], top_k=3)
            r_sim = maxsim_mean(filtered, cand.reasons) if filtered else 0.0
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            # 필터링된 reason을 강하게, fb도 함께
            sims.append(2.0 * fb_sim + 2.0 * r_sim)
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(1.0 * r_sim)
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"] and bv.reasons:
            filtered = _filtered_book_reasons(bv.reasons, fb["emb"], top_k=3)
            r_sim = maxsim_mean(filtered, cand.reasons) if filtered else 0.0
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            sims.append(-(2.0 * fb_sim + 2.0 * r_sim))
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-1.0 * r_sim)
    return float(np.mean(sims)) if sims else 0.0


def reason_score_pure_user(index, cand, good_ids, bad_ids, fb_data):
    """Mode D: 리뷰 있으면 책 reason 무시, 유저 리뷰만 사용."""
    sims = []
    for bid in good_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and not fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            sims.append(3.0 * fb_sim)  # 책 reason 완전 무시
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(1.0 * r_sim)
    for bid in bad_ids:
        bv = index.get_book(bid)
        if bv is None:
            continue
        fb = fb_data.get(bid)
        if fb and fb["is_dislike"]:
            fb_sim = max_over_cand(fb["emb"], cand.reasons)
            sims.append(-3.0 * fb_sim)
        else:
            r_sim = maxsim_mean(bv.reasons, cand.reasons) if bv.reasons else 0.0
            sims.append(-1.0 * r_sim)
    return float(np.mean(sims)) if sims else 0.0


REASON_MODES = {
    "A_engine_full": reason_score_engine_full,
    "B_fb_primary": reason_score_fb_primary,
    "C_filter": reason_score_filter,
    "D_pure_user": reason_score_pure_user,
}


def score_all(index, persona, fb_data, reason_mode_fn):
    good_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "good"]
    bad_ids = [b["book_id"] for b in persona["books"] if b["rating"] == "bad"]
    read = set(good_ids + bad_ids)

    good_books = [index.get_book(b) for b in good_ids if index.get_book(b)]
    bad_books = [index.get_book(b) for b in bad_ids if index.get_book(b)]

    scores = {}
    for cid in index.book_ids:
        if cid in read:
            continue
        cand = index.get_book(cid)
        if cand is None:
            continue

        # desc
        desc_s = max(float(np.dot(b.desc, cand.desc)) for b in good_books) if good_books else 0.0
        if bad_books:
            desc_s -= max(float(np.dot(b.desc, cand.desc)) for b in bad_books)

        # reason (mode-specific)
        reason_s = reason_mode_fn(index, cand, good_ids, bad_ids, fb_data)

        # fb_desc
        fb_desc_vals = []
        for bid, fb in fb_data.items():
            sign = -1.0 if fb["is_dislike"] else 1.0
            fb_desc_vals.append(sign * float(np.dot(fb["emb"], cand.desc)))
        fb_desc_s = float(np.mean(fb_desc_vals)) if fb_desc_vals else 0.0

        total = W_DESC * desc_s + W_REASON_BLOCK * reason_s + W_FB_DESC * fb_desc_s
        scores[cid] = total

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


def evaluate(top, persona, meta, index, fb_data):
    top_ids = [bid for bid, _ in top]
    top_l1s = [parse_l1(meta.get(bid, {}).get("genre", "")) for bid in top_ids]

    user_l1s = set()
    for b in persona["books"]:
        if b["rating"] == "good":
            m = meta.get(b["book_id"])
            if m:
                user_l1s.add(parse_l1(m.get("genre", "")))
    cov = len(user_l1s & set(top_l1s)) / len(user_l1s) if user_l1s else 0.0

    counts = Counter(top_l1s)
    tot = sum(counts.values())
    div = 1 - sum((c / tot) ** 2 for c in counts.values()) if tot else 0.0

    user_authors = set()
    for b in persona["books"]:
        if b["rating"] == "good":
            m = meta.get(b["book_id"])
            if m and m.get("author"):
                a = m["author"].split(",")[0].strip()
                if a:
                    user_authors.add(a)
    auth_hit = sum(1 for bid in top_ids
                    if meta.get(bid, {}).get("author") and
                    meta[bid]["author"].split(",")[0].strip() in user_authors)
    auth = auth_hit / len(top_ids) if top_ids else 0.0

    # ★ Taste Coherence: 추천 책 reason과 유저 리뷰의 평균 최대 유사도
    review_embs = [fb["emb"] for fb in fb_data.values() if not fb["is_dislike"]]
    if review_embs and top_ids:
        vals = []
        for bid in top_ids:
            b = index.get_book(bid)
            if b is None or not b.reasons:
                continue
            for rev in review_embs:
                vals.append(max_over_cand(rev, b.reasons))
        coherence = float(np.mean(vals)) if vals else 0.0
    else:
        coherence = None

    return dict(cov=cov, div=div, auth=auth, coherence=coherence,
                top_l1=Counter(top_l1s))


def main():
    print("=== 취향 기반 추천 모드 비교 ===\n")

    print("[setup] 인덱스")
    index, _, _ = load_index(INDEX_PATH)

    print("[setup] 페르소나")
    with open(PERSONAS_PATH) as f:
        data = json.load(f)
    personas = data["personas"]

    print("[setup] 리뷰 임베딩")
    all_reviews = list({b["review_text"] for p in personas for b in p["books"]
                         if b.get("review_text")})
    review_embs = call_embedding(all_reviews) if all_reviews else []
    review_emb_map = dict(zip(all_reviews, review_embs))

    sb = create_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")))

    print("[setup] 메타")
    all_book_ids = list(index.book_ids)
    meta = {}
    for i in range(0, len(all_book_ids), 200):
        chunk = all_book_ids[i:i + 200]
        res = sb.table("books").select("id, title, author, genre").in_("id", chunk).execute()
        for r in res.data:
            meta[r["id"]] = r
        time.sleep(0.5)
    print(f"  meta: {len(meta)}")

    def fb_for(p):
        d = {}
        for b in p["books"]:
            if b.get("review_text"):
                emb = review_emb_map.get(b["review_text"])
                if emb is not None:
                    d[b["book_id"]] = dict(emb=normalize(emb),
                                             is_dislike=b["rating"] == "bad")
        return d

    # 결과 집계
    out = ["# 취향 기반 추천 — 4가지 reason 모드 비교",
            f"\n생성: {datetime.now().isoformat()}\n"]
    out.append("## 모드 정의\n")
    out.append("| 모드 | reason 계산 방식 |")
    out.append("|---|---|")
    out.append("| A engine_full | fb_sim 3.0 + r_sim 0.5 (원본 엔진) |")
    out.append("| B fb_primary  | fb_sim 5.0 + r_sim 0.2 (유저 리뷰 극대화) |")
    out.append("| C filter      | fb_sim 2.0 + filtered r_sim 2.0 (책 reason top-k만) |")
    out.append("| D pure_user   | fb_sim 3.0 + r_sim 0 (책 reason 무시) |")

    all_metrics = {m: [] for m in REASON_MODES}

    for p in personas:
        print(f"  {p['name']}...")
        fb = fb_for(p)
        out.append(f"\n## {p['name']} — {p['profile']}\n")
        has_review = bool(fb)
        out.append(f"_리뷰 {len(fb)}개, good {sum(1 for b in p['books'] if b['rating']=='good')}_\n")
        out.append("| 모드 | Cov | Div | Auth | Coherence | 분포 |")
        out.append("|---|---|---|---|---|---|")
        persona_top = {}
        for mode_name, mode_fn in REASON_MODES.items():
            scored = score_all(index, p, fb, mode_fn)
            top = cap_dynamic(scored, meta, p)
            m = evaluate(top, p, meta, index, fb)
            all_metrics[mode_name].append(m)
            persona_top[mode_name] = top
            coh = "N/A" if m["coherence"] is None else f"{m['coherence']:.3f}"
            dist = ", ".join(f"{l}:{c}" for l, c in m["top_l1"].most_common(3))
            out.append(f"| {mode_name} | {m['cov']:.2f} | {m['div']:.2f} | "
                        f"{m['auth']:.2f} | {coh} | {dist} |")

        # 리뷰 있는 주요 페르소나는 모드별 Top 5 출력
        if has_review and p["name"] in ["서연", "민호", "준혁", "하늘"]:
            out.append("\n**모드별 Top 5 비교:**")
            for mode_name, top in persona_top.items():
                out.append(f"\n_{mode_name}:_")
                for i, (bid, score) in enumerate(top[:5], 1):
                    mm = meta.get(bid, {})
                    l1 = parse_l1(mm.get("genre", ""))
                    title = mm.get("title", bid[:8])[:55]
                    auth = (mm.get("author") or "")[:25]
                    out.append(f"  {i}. ({score:.2f}) [{l1}] {title} — {auth}")

    # 모드별 평균
    out.append("\n\n## 모드별 평균 (18 페르소나)\n")
    out.append("| 모드 | Cov | Div | Auth | Coherence (리뷰 있는 페르소나) |")
    out.append("|---|---|---|---|---|")
    for mode_name in REASON_MODES:
        ms = all_metrics[mode_name]
        cov = float(np.mean([m["cov"] for m in ms]))
        div = float(np.mean([m["div"] for m in ms]))
        auth = float(np.mean([m["auth"] for m in ms]))
        coh_vals = [m["coherence"] for m in ms if m["coherence"] is not None]
        coh = float(np.mean(coh_vals)) if coh_vals else 0.0
        out.append(f"| {mode_name} | {cov:.3f} | {div:.3f} | {auth:.3f} | {coh:.3f} |")

    # Coherence만으로 순위
    out.append("\n## Taste Coherence 순위 (핵심 — 취향 정합도)\n")
    coh_rank = []
    for mode_name in REASON_MODES:
        ms = all_metrics[mode_name]
        coh_vals = [m["coherence"] for m in ms if m["coherence"] is not None]
        coh = float(np.mean(coh_vals)) if coh_vals else 0.0
        coh_rank.append((mode_name, coh))
    coh_rank.sort(key=lambda x: x[1], reverse=True)
    for rank, (name, coh) in enumerate(coh_rank, 1):
        out.append(f"{rank}. **{name}**: {coh:.3f}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), "test_data", f"taste_aware_{ts}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"\n✓ 저장: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
