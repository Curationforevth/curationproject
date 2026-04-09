"""
취향 벡터 재계산기 — 배치 경로

가중 평균 → K-means 클러스터링 단계적 진화.
즉시 경로(RPC)가 처리하는 가중 평균을 배치에서 더 정교한 클러스터링으로 덮어쓴다.

사용법:
  python3 scripts/taste_recomputer.py                # 전체 유저 재계산
  python3 scripts/taste_recomputer.py --limit 10     # 10명만
  python3 scripts/taste_recomputer.py --user-id UUID # 특정 유저
  python3 scripts/taste_recomputer.py --status        # 현황
  python3 scripts/taste_recomputer.py --dry-run       # DB 저장 없이

의존성:
  pip install supabase python-dotenv scikit-learn numpy
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
except ImportError:
    pass

# `lib.retry.with_retry` 는 hard dependency — silent no-op fallback 은 금지.
# (과거: 패스 문제로 retry 가 통째로 no-op 되어 수백 권 drop 하고도
#  exit 0 으로 끝나는 사고가 있었음. 반드시 실제 retry 가 돌아야 한다.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.retry import with_retry  # noqa: E402
from lib.batch_fallback import save_with_size_fallback  # noqa: E402


def _is_statement_timeout(exc):
    """Supabase upsert/insert 가 Postgres statement_timeout (57014) 로 실패했는지."""
    return str(getattr(exc, "code", "") or "") == "57014"


KMEANS_VECTOR_FALLBACK = [3, 1]  # cluster row insert 가 timeout 시 축소 단계
KMEANS_MIN_BOOKS = 10
KMEANS_MAX_K = 5
SILHOUETTE_THRESHOLD = 0.2
FAVORITE_BONUS = 1.2
MIN_REVIEW_LENGTH = 50


# --- 순수 함수 ---

def feedback_depth_score(book):
    """책 1권의 피드백 깊이 스코어 (1~5)."""
    review = book.get("review_text") or ""
    tags = book.get("emotion_tags") or []
    rating = book.get("rating")

    if review and len(review) >= MIN_REVIEW_LENGTH:
        return 5
    if tags and len(tags) >= 3:
        return 4
    if tags and len(tags) >= 1:
        return 3
    if rating:
        return 2
    return 1


def feedback_weight(book):
    """가중 평균용 weight. bad 평가는 0 (제외)."""
    if book.get("rating") == "bad":
        return 0

    review = book.get("review_text") or ""
    tags = book.get("emotion_tags") or []
    rating = book.get("rating")

    w = 1.0
    if review and len(review) >= MIN_REVIEW_LENGTH:
        w = 3.0
    elif tags and len(tags) >= 1:
        w = 2.0
    elif rating:
        w = 1.5

    if book.get("is_onboarding_favorite"):
        w *= FAVORITE_BONUS

    return w


def should_upgrade_to_kmeans(book_count, current_method):
    """클러스터링 전환 여부 판단."""
    if book_count >= KMEANS_MIN_BOOKS:
        return True
    return False


# --- 재계산 클래스 ---

class TasteRecomputer:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )
        self.stats = {
            "processed": 0, "weighted_avg": 0,
            "kmeans": 0, "skipped": 0, "errors": 0,
            "drop_failed": 0, "confidence_failed": 0,
        }

    def fetch_users_with_books(self, limit=0, user_id=None):
        """피드백이 있는 유저 목록 조회."""
        if user_id:
            result = with_retry(lambda: (
                self.sb.table("user_books")
                .select("user_id")
                .eq("user_id", user_id)
                .eq("status", "read")
                .limit(1)
                .execute()
            ))
            return [user_id] if result.data else []

        all_users = set()
        offset = 0
        page_size = 1000
        while True:
            result = with_retry(lambda o=offset: (
                self.sb.table("user_books")
                .select("user_id")
                .eq("status", "read")
                .range(o, o + page_size - 1)
                .execute()
            ))
            if not result.data:
                break
            for row in result.data:
                all_users.add(row["user_id"])
            if len(result.data) < page_size:
                break
            offset += page_size

        users = list(all_users)
        if limit > 0:
            users = users[:limit]
        return users

    def fetch_user_books_with_embeddings(self, user_id):
        """유저의 읽은 책 + 임베딩 조회."""
        books_result = with_retry(lambda: (
            self.sb.table("user_books")
            .select("book_id, rating, emotion_tags, review_text")
            .eq("user_id", user_id)
            .eq("status", "read")
            .execute()
        ))
        if not books_result.data:
            return []

        book_ids = [b["book_id"] for b in books_result.data]
        # A5: (book_id, tier) composite unique 후 같은 책에 tier1/tier2 공존 가능.
        # tier desc 정렬 후 첫 row 채택 → max tier 우선.
        embeddings_result = with_retry(lambda: (
            self.sb.table("book_embeddings")
            .select("book_id, tier, embedding")
            .in_("book_id", book_ids)
            .order("tier", desc=True)
            .execute()
        ))
        emb_map = {}
        for e in (embeddings_result.data or []):
            if e["book_id"] not in emb_map:
                emb_map[e["book_id"]] = e["embedding"]

        combined = []
        for b in books_result.data:
            if b["book_id"] in emb_map:
                b["embedding"] = emb_map[b["book_id"]]
                combined.append(b)
        return combined

    def compute_weighted_average(self, books):
        """가중 평균 취향 벡터 계산."""
        vectors = []
        weights = []
        for book in books:
            w = feedback_weight(book)
            if w <= 0:
                continue
            vectors.append(np.array(book["embedding"]))
            weights.append(w)

        if not vectors:
            return None

        vectors = np.array(vectors)
        weights = np.array(weights)
        avg = np.average(vectors, axis=0, weights=weights)
        return avg.tolist()

    def compute_kmeans(self, books):
        """K-means 클러스터링. 최적 k 자동 탐색."""
        vectors = []
        weights = []
        for book in books:
            w = feedback_weight(book)
            if w <= 0:
                continue
            vectors.append(np.array(book["embedding"]))
            weights.append(w)

        if len(vectors) < KMEANS_MIN_BOOKS:
            return None

        X = np.array(vectors)
        W = np.array(weights)

        best_k = 1
        best_score = -1
        best_labels = None

        max_k = min(KMEANS_MAX_K, len(vectors) - 1)
        for k in range(2, max_k + 1):
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(X)
            score = silhouette_score(X, labels)
            if score > best_score:
                best_score = score
                best_k = k
                best_labels = labels

        if best_score < SILHOUETTE_THRESHOLD:
            return None

        clusters = []
        for c in range(best_k):
            mask = best_labels == c
            cluster_vectors = X[mask]
            cluster_weights = W[mask]
            centroid = np.average(cluster_vectors, axis=0, weights=cluster_weights)
            cluster_weight = float(cluster_weights.sum()) / float(W.sum())
            clusters.append({
                "vector": centroid.tolist(),
                "weight": round(cluster_weight, 3),
                "book_count": int(mask.sum()),
            })

        return clusters

    def save_taste_vectors(self, user_id, method, vectors_data):
        """user_taste_vectors에 저장."""
        if self.dry_run:
            return

        with_retry(lambda: (
            self.sb.table("user_taste_vectors")
            .delete()
            .eq("user_id", user_id)
            .execute()
        ))

        if method == "weighted_avg":
            row = {
                "user_id": user_id,
                "cluster_label": None,
                "vector": vectors_data,
                "weight": 1.0,
                "method": "weighted_avg",
            }
            with_retry(lambda: (
                self.sb.table("user_taste_vectors")
                .insert(row)
                .execute()
            ))
        elif method == "kmeans":
            rows = []
            for i, cluster in enumerate(vectors_data):
                rows.append({
                    "user_id": user_id,
                    "cluster_label": f"cluster_{i}",
                    "vector": cluster["vector"],
                    "weight": cluster["weight"],
                    "method": "kmeans",
                })

            def saver(chunk):
                with_retry(lambda: (
                    self.sb.table("user_taste_vectors")
                    .insert(chunk)
                    .execute()
                ))

            saved, failed = save_with_size_fallback(
                items=rows,
                saver=saver,
                fallback_sizes=KMEANS_VECTOR_FALLBACK,
                is_timeout=_is_statement_timeout,
            )
            if failed > 0:
                self.stats["drop_failed"] += failed
                print(f"  ⚠ kmeans cluster {failed}/{len(rows)}개 저장 실패 ({user_id[:8]}...)")

    def process_user(self, user_id):
        """단일 유저 처리."""
        books = self.fetch_user_books_with_embeddings(user_id)
        if not books:
            self.stats["skipped"] += 1
            return

        book_count = len([b for b in books if feedback_weight(b) > 0])

        existing = with_retry(lambda: (
            self.sb.table("user_taste_vectors")
            .select("method")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ))
        current_method = existing.data[0]["method"] if existing.data else "weighted_avg"

        if should_upgrade_to_kmeans(book_count, current_method):
            clusters = self.compute_kmeans(books)
            if clusters:
                self.save_taste_vectors(user_id, "kmeans", clusters)
                self.stats["kmeans"] += 1
                self.stats["processed"] += 1
                self._refresh_confidence(user_id)
                return

        avg = self.compute_weighted_average(books)
        if avg:
            self.save_taste_vectors(user_id, "weighted_avg", avg)
            self.stats["weighted_avg"] += 1
        else:
            self.stats["skipped"] += 1

        self.stats["processed"] += 1
        self._refresh_confidence(user_id)

    def _refresh_confidence(self, user_id):
        """추천 신뢰도 스코어 갱신 (RPC 호출).

        실패해도 본 처리는 성공이지만, 운영자가 noise 를 인식할 수 있도록
        카운트 + 처음 몇 건은 로그에 남긴다 (silent pass 금지).
        """
        if self.dry_run:
            return
        try:
            with_retry(lambda: (
                self.sb.rpc("calculate_recommendation_confidence",
                            {"target_user_id": user_id}).execute()
            ))
        except Exception as e:
            self.stats["confidence_failed"] += 1
            if self.stats["confidence_failed"] <= 5:
                print(f"  ⚠ confidence 갱신 실패 ({user_id[:8]}...): {e}")

    def run(self, limit=0, user_id=None):
        """메인 실행."""
        print(f"🔍 대상 유저 조회 중...")
        users = self.fetch_users_with_books(limit=limit, user_id=user_id)
        print(f"   {len(users)}명 발견\n")

        if not users:
            print("✅ 처리할 유저가 없습니다.")
            return 0

        for i, uid in enumerate(users):
            try:
                self.process_user(uid)
                if (i + 1) % 10 == 0 or (i + 1) <= 3:
                    prefix = "(dry-run) " if self.dry_run else ""
                    print(f"  {prefix}{i + 1}/{len(users)}: "
                          f"avg={self.stats['weighted_avg']} km={self.stats['kmeans']}")
            except Exception as e:
                self.stats["errors"] += 1
                if self.stats["errors"] <= 5:
                    print(f"  ✗ 에러 ({uid[:8]}...): {e}")

        self._print_report(len(users))
        # 어떤 종류든 실패가 있으면 caller 에게 알린다.
        if (self.stats["errors"] > 0
                or self.stats["drop_failed"] > 0
                or self.stats["confidence_failed"] > 0):
            return 1
        return 0

    def _print_report(self, total):
        s = self.stats
        prefix = "(dry-run) " if self.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}취향 벡터 재계산 결과")
        print(f"{'=' * 50}")
        print(f"  대상: {total}명")
        print(f"  처리 완료: {s['processed']}명")
        print(f"  가중 평균: {s['weighted_avg']}명")
        print(f"  K-means: {s['kmeans']}명")
        print(f"  스킵: {s['skipped']}명")
        print(f"  드롭 (저장 실패): {s['drop_failed']}건")
        print(f"  confidence 갱신 실패: {s['confidence_failed']}건")
        print(f"  에러: {s['errors']}건")
        print(f"{'=' * 50}")

    def show_status(self):
        total_users = with_retry(lambda: self.sb.table("user_books")
                                  .select("user_id", count="exact").execute())
        has_taste = with_retry(lambda: self.sb.table("user_taste_vectors")
                                .select("user_id", count="exact").execute())
        has_kmeans = with_retry(lambda: self.sb.table("user_taste_vectors")
                                 .select("user_id", count="exact")
                                 .eq("method", "kmeans").execute())

        print(f"\n{'=' * 50}")
        print("취향 벡터 현황")
        print(f"{'=' * 50}")
        print(f"  책 등록 유저: {total_users.count}명")
        print(f"  취향 벡터 있음: {has_taste.count}명")
        print(f"  K-means 활성: {has_kmeans.count}명")
        print(f"  가중 평균: {has_taste.count - has_kmeans.count}명")
        print(f"{'=' * 50}")


def main():
    parser = argparse.ArgumentParser(description="취향 벡터 배치 재계산기")
    parser.add_argument("--limit", type=int, default=0, help="최대 유저 수 (0=전부)")
    parser.add_argument("--user-id", type=str, default=None, help="특정 유저 UUID")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 테스트")
    parser.add_argument("--status", action="store_true", help="현황 조회")
    args = parser.parse_args()

    recomputer = TasteRecomputer(dry_run=args.dry_run)

    if args.status:
        recomputer.show_status()
        return 0

    return recomputer.run(limit=args.limit, user_id=args.user_id) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
