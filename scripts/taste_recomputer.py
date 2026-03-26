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

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()


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
