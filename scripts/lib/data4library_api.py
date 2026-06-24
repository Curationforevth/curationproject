"""정보나루 (data4library) API wrapper.

Pure parsing functions + thin HTTP layer. The HTTP layer is intentionally
small so it can be mocked or replaced in tests/dry runs.

Endpoints supported:
  - loanItemSrch      : 인기 대출 도서 검색 (KDC × 기간) — 메인 발견 소스
  - recommandList     : 특정 ISBN과 비슷한 책 (백카탈로그/연관작)
  - srchBooks         : 키워드 검색 (단일 토큰만 안정)
  - monthlyKeywords   : 월별 인기 키워드 (시드 확장용)
  - usageAnalysisList : 책별 누적/월별 대출수 + 키워드 + 동시대출 — loan_count 정합성 기준

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
    add_code: Optional[str] = "0",
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
    if add_code:
        p["addCode"] = add_code
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

    Empty addition_symbol → pass (recommandList often returns empty;
    we don't want to throw away the whole pipe).
    """
    sym = (book.get("addition_symbol") or "").strip()
    if not sym:
        return True
    return sym[0] == "0"


# ----- HTTP layer -----

def fetch_loan_item_page(
    api_key: str, page_no: int, page_size: int,
    start_dt: str, end_dt: str, kdc: Optional[str] = None,
    add_code: Optional[str] = "0",
    timeout: float = 60.0,
) -> dict:
    params = build_loan_item_params(api_key, page_no, page_size, start_dt, end_dt, kdc, add_code)
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


# ----- usageAnalysisList (Strategy C: loan_count 정합성 기준) -----

CO_LOAN_CAP = 50


def fetch_usage_analysis(api_key: str, isbn13: str, timeout: float = 15.0) -> dict:
    """usageAnalysisList 호출. 빈 응답은 RuntimeError 로 raise (재시도 유도).

    정상 '데이터 없음' 은 response.book.loanCnt = 0 으로 내려온다 (404 아님).
    """
    params = {"authKey": api_key, "isbn13": isbn13, "format": "json"}
    r = requests.get(f"{API_BASE}/usageAnalysisList", params=params, timeout=timeout)
    r.raise_for_status()
    if not r.text.strip():
        raise RuntimeError(f"빈 응답 body (transient 의심, isbn={isbn13})")
    return r.json()


def parse_usage_analysis(response: dict) -> dict:
    """Parse usageAnalysisList response into normalized dict.

    Returns:
        {
          'loan_count': int,           # book.loanCnt (정보나루 누적 전체)
          'loan_count_12mo': int,      # sum(loanHistory.loanCnt) 최근 12개월
          'library_keywords': list,    # keywords
          'co_loan_isbns': list,       # coLoanBooks (max CO_LOAN_CAP)
          'is_empty': bool,            # book.loanCnt == 0 AND 나머지 모두 비어있음
        }
    """
    if not response:
        return {
            "loan_count": 0, "loan_count_12mo": 0,
            "library_keywords": [], "co_loan_isbns": [],
            "is_empty": True,
        }
    resp = response.get("response", {}) or {}
    book = resp.get("book", {}) or {}

    try:
        loan_count = int(book.get("loanCnt") or 0)
    except (TypeError, ValueError):
        loan_count = 0

    lh = resp.get("loanHistory", []) or []
    loan_count_12mo = 0
    for h in lh:
        loan = (h or {}).get("loan", {}) or {}
        try:
            loan_count_12mo += int(loan.get("loanCnt") or 0)
        except (TypeError, ValueError):
            pass

    keywords = []
    for kw in resp.get("keywords", []) or []:
        w = (kw or {}).get("keyword", {}) or {}
        word = w.get("word")
        if word:
            keywords.append(word)

    co_loan_isbns = []
    for b in resp.get("coLoanBooks", []) or []:
        book_dict = (b or {}).get("book", {}) or {}
        isbn = book_dict.get("isbn13")
        if isbn:
            co_loan_isbns.append(isbn)
    co_loan_isbns = co_loan_isbns[:CO_LOAN_CAP]

    is_empty = (loan_count == 0 and loan_count_12mo == 0
                and not keywords and not co_loan_isbns)

    return {
        "loan_count": loan_count,
        "loan_count_12mo": loan_count_12mo,
        "library_keywords": keywords,
        "co_loan_isbns": co_loan_isbns,
        "is_empty": is_empty,
    }
