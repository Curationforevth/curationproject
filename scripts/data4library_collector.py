"""
정보나루 도서관 데이터 수집기

usageAnalysisList API 1콜로 키워드 + 함께 빌린 책을 동시 수집.
키워드는 Tier2 임베딩 보강용, 연관도서는 Phase 3 추천 엔진용.

사용법:
  python3 scripts/data4library_collector.py                  # 기본 (300권)
  python3 scripts/data4library_collector.py --limit 50       # 50권만
  python3 scripts/data4library_collector.py --limit 10000    # 백필
  python3 scripts/data4library_collector.py --status          # 진행 현황
  python3 scripts/data4library_collector.py --dry-run         # DB 저장 없이 테스트

의존성:
  pip install requests supabase python-dotenv
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

try:
    import requests
except ImportError:
    pass

try:
    from lib.retry import with_retry
except ImportError:
    def with_retry(fn, **kwargs):
        return fn()


CO_LOAN_CAP = 50
REQUEST_DELAY = 0.5


# --- 순수 함수 (테스트 가능) ---

def parse_keywords(response):
    """API 응답에서 키워드 리스트 추출."""
    if not response:
        return []
    try:
        keywords = response.get("response", {}).get("keywords", [])
        return [
            kw["keyword"]["word"]
            for kw in keywords
            if kw.get("keyword", {}).get("word")
        ]
    except (KeyError, TypeError):
        return []


def parse_co_loan_books(response):
    """API 응답에서 함께 빌린 책 ISBN 리스트 추출. 최대 50개."""
    if not response:
        return []
    try:
        books = response.get("response", {}).get("coLoanBooks", [])
        isbns = [
            b["book"]["isbn13"]
            for b in books
            if b.get("book", {}).get("isbn13")
        ]
        return isbns[:CO_LOAN_CAP]
    except (KeyError, TypeError):
        return []
