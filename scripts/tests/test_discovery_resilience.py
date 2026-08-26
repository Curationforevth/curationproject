"""discovery 회복력 — 정보나루가 느린 러너 경로에서도 신규 도서를 저장한다.

배경(2026-08-26 실측): Daily Pipeline 이 8월 25/25 전부 cancelled(job timeout).
GitHub Actions 러너(해외 IP)→data4library.kr 경로에서 usageAnalysisList 가
연속 Read timeout → ①타임아웃마다 row 를 통째로 버리고(`continue`) ②upsert 는
루프 **끝**에서 한 번만 하므로 타임아웃 사망 시 **0권 저장**. 결과: 7/3 이후
신규 유입 0권(54일). 같은 ISBN 을 한국 IP 에서 부르면 평균 1.82s 로 정상.

→ 정보나루를 저장 경로에서 분리한다([[feedback_accumulate_not_realtime_api]],
[[feedback_data_lifecycle_refine]]): loan_count 를 못 얻어도 책은 저장하고
(loan_count=NULL) refresh_loan_count 크론이 나중에 채운다. 추가로 연속 실패 시
회로를 열어 남은 호출을 건너뛰고, 예산 소진 시 중단하며, 중간중간 flush 한다.
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _row(isbn: str, title: str = "책제목"):
    return {
        "isbn13": isbn,
        "title": title,
        "author_raw": "저자",
        "publisher": "출판사",
        "cover_url": "http://x/c.jpg",
        "loan_count": 7,
        "class_name": "문학",
    }


class _FakeDedup:
    """항상 NEW 판정 — dedup 자체는 별도 테스트 대상."""

    def __init__(self):
        self.registered = []

    def check(self, title, author, isbn, loan_count):
        import data4library_discovery_collector as mod
        self.last_loan_count = loan_count
        return (mod.DedupAction.NEW, None)

    def register(self, *a, **kw):
        self.registered.append((a, kw))

    def update_loan_count(self, *a, **kw):
        pass


def _collector(monkeypatch, fetch_impl, *, budget=None):
    """필터/DB 를 무접촉으로 만든 DiscoveryCollector 를 만든다."""
    import data4library_discovery_collector as mod

    c = mod.DiscoveryCollector(dry_run=False)
    c._api_key = "test-key"
    c._sb = object()          # 실제 호출은 아래 monkeypatch 로 차단
    c._dedup = _FakeDedup()
    if budget is not None:
        c.set_budget(budget)

    monkeypatch.setattr(mod, "fetch_usage_analysis", fetch_impl)
    monkeypatch.setattr(mod, "is_adult_general", lambda r: True)
    monkeypatch.setattr(mod, "is_non_book", lambda d: False)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    upserted_batches = []

    def fake_upsert(sb, rows, chunk_size=200):
        upserted_batches.append(list(rows))
        return len(rows)

    monkeypatch.setattr(mod, "upsert_books_rich_merge", fake_upsert)
    monkeypatch.setattr(mod.DiscoveryCollector, "_apply_usage_fields",
                        lambda self, rows: None)
    return mod, c, upserted_batches


def _timeout(api_key, isbn, timeout=15.0):
    raise requests.exceptions.ReadTimeout("read timeout")


def _ok(api_key, isbn, timeout=15.0):
    return {"ok": True}


# ---------------------------------------------------------------------------
# ① transient 실패해도 신규 도서는 저장된다 (핵심 회귀)
# ---------------------------------------------------------------------------

def test_transient_usage_still_saves_book_with_unknown_loan_count(monkeypatch):
    mod, c, batches = _collector(monkeypatch, _timeout)

    upserted = c.filter_and_upsert([_row("9791100000001")])

    assert upserted == 1, "정보나루 타임아웃이 신규 도서 저장을 막으면 안 된다"
    saved = [r for b in batches for r in b]
    assert len(saved) == 1
    assert saved[0]["isbn"] == "9791100000001"
    assert saved[0]["loan_count"] is None, (
        "loan_count 는 '모름'(NULL) 이어야 refresh_loan_count 크론이 채운다. "
        "0 으로 저장하면 '대출 0회로 확인됨' 이라는 거짓 사실이 축적된다")
    assert c.stats["usage_unknown_saved"] == 1


def test_unknown_loan_count_is_conservative_in_dedup(monkeypatch):
    """loan_count 를 모르면 기존 에디션을 밀어내지 않는다(0 으로 비교)."""
    mod, c, _ = _collector(monkeypatch, _timeout)

    c.filter_and_upsert([_row("9791100000002")])

    assert c._dedup.last_loan_count == 0


def test_no_data_still_saves_zero_not_null(monkeypatch):
    """빈 응답(미수록 확정)은 기존대로 0 으로 저장 — '모름'과 구분된다."""
    def empty(api_key, isbn, timeout=15.0):
        raise RuntimeError("빈 응답 body")

    mod, c, batches = _collector(monkeypatch, empty)
    c.filter_and_upsert([_row("9791100000003")])

    saved = [r for b in batches for r in b][0]
    assert saved["loan_count"] == 0, "미수록 확정은 0 (모름=NULL 과 구분)"
    assert c.stats["usage_unknown_saved"] == 0


# ---------------------------------------------------------------------------
# ② 연속 실패 시 회로 차단 — 남은 예산을 타임아웃으로 태우지 않는다
# ---------------------------------------------------------------------------

def test_circuit_opens_after_consecutive_failures(monkeypatch):
    mod, c, _ = _collector(monkeypatch, _timeout)
    calls = []

    def counting(api_key, isbn, timeout=15.0):
        calls.append(isbn)
        raise requests.exceptions.ReadTimeout("read timeout")

    monkeypatch.setattr(mod, "fetch_usage_analysis", counting)

    rows = [_row(f"979110000{i:04d}") for i in range(20)]
    c.filter_and_upsert(rows)

    limit = mod.USAGE_FAIL_STREAK_LIMIT
    assert len(calls) == limit, (
        f"연속 {limit}회 실패 후엔 호출을 멈춰야 한다 (실제 {len(calls)}회). "
        "이게 25분 예산을 통째로 태우던 원인")
    assert c._usage_circuit_open is True
    assert c.stats["usage_circuit_skipped"] == 20 - limit
    assert c.stats["upserted"] == 20, "회로가 열려도 책은 전부 저장된다"


def test_circuit_stays_closed_when_calls_succeed(monkeypatch):
    mod, c, _ = _collector(monkeypatch, _ok)
    monkeypatch.setattr(mod, "parse_usage_analysis",
                        lambda raw: {"loan_count": 5, "loan_count_12mo": 2})

    c.filter_and_upsert([_row(f"979110000{i:04d}") for i in range(12)])

    assert c._usage_circuit_open is False
    assert c.stats["usage_circuit_skipped"] == 0


def test_success_resets_fail_streak(monkeypatch):
    """간헐적 실패는 회로를 열지 않는다 — 성공이 연속 카운터를 리셋."""
    mod, c, _ = _collector(monkeypatch, _ok)
    seq = {"i": 0}

    def flaky(api_key, isbn, timeout=15.0):
        seq["i"] += 1
        if seq["i"] % 2 == 1:
            raise requests.exceptions.ReadTimeout("read timeout")
        return {"ok": True}

    monkeypatch.setattr(mod, "fetch_usage_analysis", flaky)
    monkeypatch.setattr(mod, "parse_usage_analysis",
                        lambda raw: {"loan_count": 5, "loan_count_12mo": 2})

    c.filter_and_upsert([_row(f"979110000{i:04d}") for i in range(20)])

    assert c._usage_circuit_open is False, "번갈아 실패는 연속 실패가 아니다"


# ---------------------------------------------------------------------------
# ③ 중간 flush — 강제 종료돼도 진행분이 남는다
# ---------------------------------------------------------------------------

def test_flushes_incrementally(monkeypatch):
    mod, c, batches = _collector(monkeypatch, _timeout)

    n = mod.FLUSH_EVERY * 2 + 3
    c.filter_and_upsert([_row(f"97911{i:08d}") for i in range(n)])

    assert len(batches) >= 3, (
        f"{mod.FLUSH_EVERY}권마다 저장해야 job 이 죽어도 진행분이 남는다 "
        f"(실제 batch {len(batches)}개)")
    assert sum(len(b) for b in batches) == n


# ---------------------------------------------------------------------------
# ④ 시간 예산 — job timeout 전에 스스로 멈추고 flush
# ---------------------------------------------------------------------------

def test_budget_exhaustion_stops_and_flushes(monkeypatch):
    mod, c, batches = _collector(monkeypatch, _ok, budget=10.0)
    monkeypatch.setattr(mod, "parse_usage_analysis",
                        lambda raw: {"loan_count": 5, "loan_count_12mo": 2})

    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    c.set_budget(10.0)          # deadline = 1010

    processed = {"n": 0}
    real_ok = _ok

    def ticking(api_key, isbn, timeout=15.0):
        processed["n"] += 1
        clock["t"] += 4.0       # 호출당 4s → 3번째에 예산 초과
        return real_ok(api_key, isbn, timeout)

    monkeypatch.setattr(mod, "fetch_usage_analysis", ticking)

    c.filter_and_upsert([_row(f"97911{i:08d}") for i in range(50)])

    assert processed["n"] < 50, "예산을 넘기면 남은 row 를 붙들지 않고 멈춰야 한다"
    assert c.stats["budget_exhausted"] == 1
    assert sum(len(b) for b in batches) == processed["n"], (
        "중단 시점까지 처리한 row 는 전부 저장돼 있어야 한다(유실 금지)")


def test_no_budget_means_no_limit(monkeypatch):
    mod, c, batches = _collector(monkeypatch, _ok)
    monkeypatch.setattr(mod, "parse_usage_analysis",
                        lambda raw: {"loan_count": 5, "loan_count_12mo": 2})

    c.filter_and_upsert([_row(f"97911{i:08d}") for i in range(30)])

    assert c.stats["budget_exhausted"] == 0
    assert sum(len(b) for b in batches) == 30


# ---------------------------------------------------------------------------
# ⑤ sanitize — '모름' 을 0 으로 위조하지 않는다
# ---------------------------------------------------------------------------

def test_sanitize_preserves_unknown_loan_count():
    from data4library_discovery_collector import sanitize_for_upsert

    row = sanitize_for_upsert({"isbn13": "9791100000009", "title": "t",
                               "author_raw": "a", "loan_count": None})
    assert row["loan_count"] is None

    row0 = sanitize_for_upsert({"isbn13": "9791100000010", "title": "t",
                                "author_raw": "a", "loan_count": 0})
    assert row0["loan_count"] == 0


# ---------------------------------------------------------------------------
# ⑥ exit code — 외부 API 산발 실패로 job 을 죽이지 않는다(PR#54 규약과 동일)
# ---------------------------------------------------------------------------

def _stats(**kw):
    base = {"fetched_raw": 400, "pages_attempted": 10, "fetch_errors": 0,
            "errors": 0, "upserted": 362}
    base.update(kw)
    return base


def test_exit_zero_when_clean():
    from data4library_discovery_collector import resolve_exit_code
    code, msg = resolve_exit_code(_stats())
    assert code == 0


def test_exit_one_on_db_write_errors():
    """DB 쓰기 실패는 산발이어도 fail-loud (KI-002)."""
    from data4library_discovery_collector import resolve_exit_code
    code, msg = resolve_exit_code(_stats(errors=1))
    assert code == 1
    assert "쓰기" in msg or "errors" in msg


def test_exit_zero_on_sporadic_fetch_errors():
    """실측 케이스: KDC 10개 중 2개 실패했지만 400권 수집·362권 저장 → exit 0.

    이걸 exit 1 로 두면 daily-pipeline 이 매일 failure 로 남아 진짜 장애를
    가리고, 같은 job 의 backfill_genre 스텝까지 통째로 건너뛴다.
    """
    from data4library_discovery_collector import resolve_exit_code
    code, msg = resolve_exit_code(_stats(fetch_errors=2))
    assert code == 0, msg
    assert "2" in msg, "에러 건수는 메시지에 그대로 남아야 한다(축소 보고 금지)"


def test_exit_one_when_most_pages_fail():
    from data4library_discovery_collector import resolve_exit_code
    code, msg = resolve_exit_code(_stats(fetch_errors=8))
    assert code == 1


def test_exit_one_when_nothing_fetched():
    from data4library_discovery_collector import resolve_exit_code
    code, msg = resolve_exit_code(_stats(fetched_raw=0, fetch_errors=10, upserted=0))
    assert code == 1
