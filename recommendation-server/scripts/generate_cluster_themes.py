"""recommendation-server/scripts/generate_cluster_themes.py

Monthly: KMeans(desc_matrix) → book_cluster_assignments upsert →
cluster 대표 책 5개 추출 → OpenAI gpt-4o-mini로 title/description 생성 →
curation_themes (theme_type='cluster') upsert.

index.pkl 을 LFS 로 pull하여 Supabase egress 회피.

feedback_batch_operations:
- per-cluster try/except
- sleep 1s per OpenAI call (rate limit)
- dry-run 지원
- 생성/갱신 count 로깅
"""
from __future__ import annotations
import os, sys, time, pickle
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_supabase

N_CLUSTERS = 30
INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "index.pkl")

FORBIDDEN_WORDS = ("최고", "1위", "베스트", "무조건", "반드시")


def _fallback_title(cluster_id: int, sample_titles: list[str]) -> tuple[str, str]:
    return (f"묶음 #{cluster_id}", "비슷한 분위기의 책들")


def _generate_llm_title(sample_titles: list[str], sample_reasons: list[str]) -> tuple[str, str]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = f"""아래 5권의 책들이 비슷한 분위기로 묶입니다. 한국어로 큐레이션 제목과 설명을 생성해주세요.

책:
{chr(10).join(f'- {t}' for t in sample_titles)}

감상 키워드:
{chr(10).join(f'- {r}' for r in sample_reasons[:3])}

형식:
제목: (5~30자, 광고문구 금지)
설명: (한 줄, 50자 이내)
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200, temperature=0.7,
    )
    text = resp.choices[0].message.content or ""
    title = ""
    description = ""
    for line in text.splitlines():
        if line.startswith("제목:"):
            title = line[3:].strip()
        elif line.startswith("설명:"):
            description = line[3:].strip()

    if not (5 <= len(title) <= 30) or any(w in title for w in FORBIDDEN_WORDS):
        return ("", "")  # fallback caller
    return title, description


def main(dry_run: bool = False):
    print(f"[cluster] dry_run={dry_run}")
    with open(INDEX_PATH, "rb") as f:
        bundle = pickle.load(f)

    desc_matrix = bundle["desc_matrix_f16"].astype("float32")
    bid_order = bundle["bid_order"]
    meta = bundle["meta"]

    from sklearn.cluster import KMeans
    import numpy as np

    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init="auto")
    labels = km.fit_predict(desc_matrix)
    centers = km.cluster_centers_

    # 각 책의 cluster 내 distance
    distances = np.linalg.norm(desc_matrix - centers[labels], axis=1)

    cluster_version = datetime.utcnow().strftime("v%Y%m")
    print(f"  cluster_version: {cluster_version}")

    sb = get_supabase()

    # book_cluster_assignments upsert
    assign_rows = []
    for i, bid in enumerate(bid_order):
        assign_rows.append({
            "book_id": bid, "cluster_id": int(labels[i]),
            "cluster_version": cluster_version, "distance": float(distances[i]),
        })

    if not dry_run:
        # chunk upsert (1000 per batch)
        BATCH = 1000
        for i in range(0, len(assign_rows), BATCH):
            sb.table("book_cluster_assignments").upsert(
                assign_rows[i:i+BATCH], on_conflict="book_id"
            ).execute()
        print(f"  assignments: {len(assign_rows)}")

    # 각 cluster 에 대해 LLM title/description + curation_themes upsert
    created = 0
    for cluster_id in range(N_CLUSTERS):
        try:
            idxs = np.where(labels == cluster_id)[0]
            if len(idxs) == 0:
                continue
            # centroid 가장 가까운 5개
            sub_dist = distances[idxs]
            top5 = idxs[np.argsort(sub_dist)[:5]]
            sample_bids = [bid_order[i] for i in top5]
            sample_titles = [meta.get(b, {}).get("title", "") for b in sample_bids]
            sample_reasons: list[str] = []  # reason 은 spec 에서 선택, 생략 가능

            title = ""
            description = ""
            if not dry_run and os.environ.get("OPENAI_API_KEY"):
                try:
                    title, description = _generate_llm_title(sample_titles, sample_reasons)
                    time.sleep(1)  # rate limit
                except Exception as e:
                    print(f"  [LLM fail] cluster {cluster_id}: {e}")

            if not title:
                title, description = _fallback_title(cluster_id, sample_titles)

            if not dry_run:
                sb.table("curation_themes").upsert({
                    "theme_key": f"cluster|{cluster_id}",
                    "theme_type": "cluster",
                    "title": title,
                    "description": description,
                    "selection_query": {"type": "cluster"},
                    "parameters": {
                        "cluster_id": cluster_id,
                        "cluster_version": cluster_version,
                    },
                    "personalization": "general",
                    "is_active": True,
                }, on_conflict="theme_key").execute()
            created += 1
        except Exception as e:
            print(f"  [skip] cluster {cluster_id}: {e}")

    print(f"  clusters upserted: {created}/{N_CLUSTERS}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
