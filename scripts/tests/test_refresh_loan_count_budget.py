"""refresh_loan_count 시간 예산 — job timeout 강제종료 대신 스스로 멈춘다.

2026-08-25 실측: 200권 중 **3권** 처리하고 15분 job timeout 에 걸려 cancelled.
호출이 실패한 게 아니라 **성공하는데 느렸다**(러너→정보나루 경로, 60s timeout +
backoff 재시도) → MAX_CONSECUTIVE_ERRORS 조기중단도 안 걸린다. 예산을 넘기면
정상 종료(exit 0)해야 ①job 이 성공으로 남고 ②다음 run 이 nullsfirst 로 이어받는다.

discovery 가 loan_count=NULL 로 책을 저장하게 되면서 이 스크립트가 유일한
채움 경로가 됐다 — 조용히 죽으면 안 된다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _refresher(monkeypatch, per_book_seconds, n_candidates=200):
    import refresh_loan_count as mod

    r = mod.LoanCountRefresher(dry_run=False)
    r._api_key = "test-key"

    clock = {"t": 500.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    books = [{"id": f"b{i}", "isbn": f"97911{i:08d}", "title": f"t{i}"}
             for i in range(n_candidates)]
    monkeypatch.setattr(mod.LoanCountRefresher, "fetch_stale",
                        lambda self, limit=200: books[:limit])

    processed = []

    def slow_refresh(self, book):
        processed.append(book["id"])
        clock["t"] += per_book_seconds
        self.stats["updated"] += 1
        return "updated"

    monkeypatch.setattr(mod.LoanCountRefresher, "refresh_one", slow_refresh)
    return mod, r, processed


def test_stops_on_budget_and_exits_zero(monkeypatch):
    mod, r, processed = _refresher(monkeypatch, per_book_seconds=20.0)
    r.set_budget(100.0)          # 100s / 20s per book → ~5권

    rc = r.run(limit=200)

    assert len(processed) < 200, "예산을 넘기면 멈춰야 한다"
    assert len(processed) <= 6
    assert rc == 0, ("예산 소진은 실패가 아니다 — exit 0 이어야 job 이 성공으로 "
                     "남고 다음 run 이 이어받는다")
    assert r.stats["budget_exhausted"] == 1


def test_no_budget_processes_everything(monkeypatch):
    mod, r, processed = _refresher(monkeypatch, per_book_seconds=20.0,
                                   n_candidates=12)

    rc = r.run(limit=12)

    assert len(processed) == 12
    assert rc == 0
    assert r.stats["budget_exhausted"] == 0


def test_budget_not_exhausted_when_fast(monkeypatch):
    mod, r, processed = _refresher(monkeypatch, per_book_seconds=0.1,
                                   n_candidates=30)
    r.set_budget(600.0)

    rc = r.run(limit=30)

    assert len(processed) == 30
    assert rc == 0
    assert r.stats["budget_exhausted"] == 0
