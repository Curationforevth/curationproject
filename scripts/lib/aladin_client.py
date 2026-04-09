"""
알라딘 API 클라이언트
- ItemList / ItemSearch 호출
- 레이트 리밋 추적
- 재시도 (exponential backoff)
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error


class AladinAPIError(Exception):
    """Aladin API 호출 실패 (retry 소진 등). 호출자가 transient 여부 판단."""
    pass


class AladinClient:
    ITEM_LIST_URL = "http://www.aladin.co.kr/ttb/api/ItemList.aspx"
    ITEM_SEARCH_URL = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"

    def __init__(self, ttb_key, daily_limit=4900):
        self.ttb_key = ttb_key
        self.daily_limit = daily_limit
        self.api_calls = 0

    @property
    def remaining_calls(self):
        return self.daily_limit - self.api_calls

    def has_budget(self):
        return self.api_calls < self.daily_limit

    def _request(self, url, params, max_retries=3):
        """HTTP 요청 + 재시도"""
        params["ttbkey"] = self.ttb_key
        params["output"] = "js"
        params["Version"] = "20131101"
        params["SearchTarget"] = "Book"

        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(full_url, timeout=15) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self.api_calls += 1
                return data
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"    ⚠ API 오류, {wait}초 후 재시도: {e}")
                    time.sleep(wait)
                else:
                    print(f"    ✗ API 호출 실패 (재시도 소진): {e}")
                    raise AladinAPIError(f"retries exhausted: {e}") from e
        raise AladinAPIError("unreachable")

    def fetch_item_list(self, query_type, category_id=None, page=1, max_results=50):
        """ItemList API — 베스트셀러, 신간, 편집자추천 등"""
        params = {
            "QueryType": query_type,
            "MaxResults": max_results,
            "start": page,
        }
        if category_id:
            params["CategoryId"] = category_id

        data = self._request(self.ITEM_LIST_URL, params)
        if not data:
            return [], 0

        items = data.get("item", [])
        total_results = data.get("totalResults", 0)
        return items, total_results

    def search_books(self, keyword, page=1, max_results=50, sort="SalesPoint"):
        """ItemSearch API — 키워드 검색 (판매량순 정렬)"""
        params = {
            "Query": keyword,
            "QueryType": "Keyword",
            "MaxResults": max_results,
            "start": page,
            "Sort": sort,
        }

        data = self._request(self.ITEM_SEARCH_URL, params)
        if not data:
            return [], 0

        items = data.get("item", [])
        total_results = data.get("totalResults", 0)
        return items, total_results
