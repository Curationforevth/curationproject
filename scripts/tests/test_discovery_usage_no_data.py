"""discovery — usageAnalysisList 빈응답을 no_data 로 축적(재discovery 방지) 테스트.

미수록 ISBN(신간)에 usageAnalysisList 가 빈응답(RuntimeError)이면, 기존엔 None
반환 → row skip → DB 미저장 → 다음 run 또 '신규' 로 보여 재호출(무한). 빈응답은
no_data(loan_count=0)로 확정해 저장해야 dedup index 에 올라 다음 run SKIP 된다.
([[feedback_accumulate_not_realtime_api]]) transient 만 None(skip, 재시도).
"""
import sys
import os

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_empty_usage_returns_no_data_not_none(monkeypatch):
    import data4library_discovery_collector as mod
    c = mod.DiscoveryCollector(dry_run=False)
    c._api_key = "test-key"  # lazy property 우회 (env/DB 무접촉)

    def fake_fetch(api_key, isbn, timeout=15.0):
        raise RuntimeError(f"빈 응답 body (transient 의심, isbn={isbn})")
    monkeypatch.setattr(mod, "fetch_usage_analysis", fake_fetch)

    result = c._fetch_accurate_loan_count("9791199999999")

    assert result is not None, "빈응답은 no_data 로 저장돼야 함 (None=skip 금지)"
    assert result.get("is_empty") is True
    assert result.get("loan_count") == 0
    assert c.stats["usage_no_data"] == 1


def test_transient_usage_returns_none(monkeypatch):
    import data4library_discovery_collector as mod
    c = mod.DiscoveryCollector(dry_run=False)
    c._api_key = "test-key"

    def fake_fetch(api_key, isbn, timeout=15.0):
        raise requests.exceptions.Timeout("read timeout")
    monkeypatch.setattr(mod, "fetch_usage_analysis", fake_fetch)

    result = c._fetch_accurate_loan_count("9788900000001")

    assert result is None, "transient 은 skip(None) 후 다음 run 재시도"
    assert c.stats["usage_api_errors"] == 1
