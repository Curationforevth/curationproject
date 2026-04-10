"""정보나루 (data4library) API wrapper.

Pure parsing functions + thin HTTP layer. The HTTP layer is intentionally
small so it can be mocked or replaced in tests/dry runs.

Endpoints supported:
  - loanItemSrch    : 인기 대출 도서 검색 (KDC × 기간) — 메인 발견 소스
  - recommandList   : 특정 ISBN과 비슷한 책 (백카탈로그/연관작)
  - srchBooks       : 키워드 검색 (단일 토큰만 안정)
  - monthlyKeywords : 월별 인기 키워드 (시드 확장용)

Response shape note: loanItemSrch wraps each result under `doc`,
recommandList wraps under `book`. parse_book_docs handles both.
"""
from __future__ import annotations

import re
from typing import Optional

import requests


API_BASE = "http://data4library.kr/api"


# ----- param builders -----

def build_loan_item_params(
    api_key: str, page_no: int, page_size: int,
    start_dt: str, end_dt: str, kdc: Optional[str] = None,
) -> dict:
    p = {
        "authKey": api_key,
        "format": "json",
        "pageNo": page_no,
        "pageSize": page_size,
        "startDt": start_dt,
        "endDt": end_dt,
    }
    if kdc:
        p["kdc"] = kdc
    return p


def build_recommand_params(api_key: str, isbn13: str, page_size: int = 10) -> dict:
    return {
        "authKey": api_key,
        "format": "json",
        "isbn13": isbn13,
        "pageNo": 1,
        "pageSize": page_size,
    }


def build_search_params(
    api_key: str, keyword: str, page_no: int = 1, page_size: int = 10,
) -> dict:
    return {
        "authKey": api_key,
        "format": "json",
        "keyword": keyword,
        "pageNo": page_no,
        "pageSize": page_size,
    }


def build_monthly_keywords_params(api_key: str, month: str) -> dict:
    """month: 'YYYY-MM'"""
    return {
        "authKey": api_key,
        "format": "json",
        "month": month,
    }


# ----- parsing -----

def _clean_title(raw: str) -> str:
    """Strip whitespace; collapse internal whitespace.

    We keep the colon-style subtitle intact because dedup_checker normalizes
    it later.
    """
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def parse_book_docs(response: dict) -> list[dict]:
    """Parse a books-list response (loanItemSrch OR recommandList).

    Both endpoints return docs as a list under response.docs, but each item
    is wrapped under either `doc` (loanItemSrch) or `book` (recommandList).
    Books without isbn13 are skipped.

    Returns rows with normalized field names ready for downstream filtering.
    """
    if not response:
        return []
    docs = response.get("response", {}).get("docs", [])
    out: list[dict] = []
    for item in docs:
        d = item.get("doc") or item.get("book") or item
        isbn = (d.get("isbn13") or "").strip()
        if not isbn:
            continue
        loan_raw = d.get("loan_count") or "0"
        try:
            loan_count = int(loan_raw)
        except (TypeError, ValueError):
            loan_count = 0
        out.append({
            "isbn13": isbn,
            "title": _clean_title(d.get("bookname") or ""),
            "author_raw": (d.get("authors") or "").strip(),
            "publisher": (d.get("publisher") or "").strip() or None,
            "publication_year": (d.get("publication_year") or "").strip() or None,
            "addition_symbol": (d.get("addition_symbol") or "").strip(),
            "kdc": (d.get("class_no") or "").strip() or None,
            "cover_url": (d.get("bookImageURL") or "").strip() or None,
            "loan_count": loan_count,
        })
    return out


def parse_monthly_keywords(response: dict) -> list[tuple[str, float]]:
    """Return [(word, weight), ...] from monthlyKeywords response."""
    if not response:
        return []
    kws = response.get("response", {}).get("keywords", [])
    out: list[tuple[str, float]] = []
    for item in kws:
        kw = item.get("keyword") or {}
        word = kw.get("word") if isinstance(kw, dict) else None
        if not word:
            continue
        try:
            weight = float(kw.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0.0
        out.append((word, weight))
    return out


def is_adult_general(book: dict) -> bool:
    """Adult general filter: addition_symbol[0] == '0'.

    First digit meanings:
      0 = 단행본 (성인 일반)  ← target
      5 = 청소년
      6 = 대학
      7 = 아동
      8 = 학습참고서
      9 = 만화

    Empty addition_symbol → reject (unknown target audience, safer to skip).
    recommandList 에서 빈 값이 오면 성인 일반 여부를 확인할 수 없으므로
    False 로 처리하여 후속 필터에서 제외.
    """
    sym = (book.get("addition_symbol") or "").strip()
    if not sym:
        return False
    return sym[0] == "0"


# ----- HTTP layer -----

def fetch_loan_item_page(
    api_key: str, page_no: int, page_size: int,
    start_dt: str, end_dt: str, kdc: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    params = build_loan_item_params(api_key, page_no, page_size, start_dt, end_dt, kdc)
    r = requests.get(f"{API_BASE}/loanItemSrch", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_recommand(api_key: str, isbn13: str, page_size: int = 10,
                    timeout: float = 60.0) -> dict:
    params = build_recommand_params(api_key, isbn13, page_size)
    r = requests.get(f"{API_BASE}/recommandList", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_search(api_key: str, keyword: str, page_no: int = 1, page_size: int = 10,
                 timeout: float = 60.0) -> dict:
    params = build_search_params(api_key, keyword, page_no, page_size)
    r = requests.get(f"{API_BASE}/srchBooks", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_monthly_keywords(api_key: str, month: str, timeout: float = 60.0) -> dict:
    params = build_monthly_keywords_params(api_key, month)
    r = requests.get(f"{API_BASE}/monthlyKeywords", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()
