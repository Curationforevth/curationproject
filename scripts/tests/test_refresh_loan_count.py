"""refresh_loan_count — 빈응답(미수록 ISBN) negative caching 테스트.

data4library 는 미수록 ISBN(신간 979-11-…)에 HTTP 200 + 빈 body 를 반환하고
fetch_usage_analysis 는 이를 RuntimeError 로 raise 한다. 이를 transient error 로
취급하면 → update 안 함 → loan_count_updated_at NULL 유지 → 매 run 같은 죽은 ISBN
재호출(=무한). 빈응답은 no_data 로 확정 + updated_at stamp 해서 큐 뒤로 보내야 한다.
(Eden 원칙: DB에 축적하고 매번 API 재호출 금지.)
"""
import sys
import os

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_refresher(monkeypatch, no_sleep=True):
    import refresh_loan_count as mod
    r = mod.LoanCountRefresher(dry_run=False)
    r._api_key = "test-key"
    r._sb = object()  # update_loan_count_by_book_id 는 monkeypatch 로 대체
    if no_sleep:
        monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    return mod, r


def test_empty_body_is_no_data_and_stamps_updated_at(monkeypatch):
    """빈응답 → error 아님, no_data 로 확정 + update 호출(updated_at stamp), exit 0."""
    mod, r = _make_refresher(monkeypatch)

    def fake_fetch(api_key, isbn, timeout=60.0):
        raise RuntimeError(f"빈 응답 body (transient 의심, isbn={isbn})")
    monkeypatch.setattr(mod, "fetch_usage_analysis", fake_fetch)

    captured = []
    def fake_update(sb, book_id, loan_count, loan_count_12mo,
                    source="usageAnalysisList", extra=None):
        captured.append({"book_id": book_id, "loan_count": loan_count})
    monkeypatch.setattr(mod, "update_loan_count_by_book_id", fake_update)

    monkeypatch.setattr(
        r, "fetch_stale",
        lambda limit: [{"id": "b1", "isbn": "9791199999999", "title": "신간"}],
    )

    exit_code = r.run(limit=1)

    assert exit_code == 0, "no_data 만 있으면 job 은 정상 종료해야 함"
    assert r.stats["errors"] == 0, "빈응답을 error 로 세면 안 됨"
    assert len(captured) == 1, "no_data 도 updated_at stamp 위해 update 호출돼야 함"
    assert captured[0]["loan_count"] == 0


def test_transient_error_is_not_stamped(monkeypatch):
    """진짜 transient(connection) → error 카운트, update 호출 안 함(재시도 위해 NULL 유지)."""
    mod, r = _make_refresher(monkeypatch)

    def fake_fetch(api_key, isbn, timeout=60.0):
        raise requests.exceptions.ConnectionError("conn reset")
    monkeypatch.setattr(mod, "fetch_usage_analysis", fake_fetch)

    captured = []
    monkeypatch.setattr(
        mod, "update_loan_count_by_book_id",
        lambda *a, **k: captured.append(a),
    )
    monkeypatch.setattr(
        r, "fetch_stale",
        lambda limit: [{"id": "b1", "isbn": "9788900000001", "title": "책"}],
    )

    r.run(limit=1)

    assert r.stats["errors"] == 1, "transient 는 error 로 카운트"
    assert len(captured) == 0, "transient 는 stamp 하면 안 됨(다음 run 재시도)"
