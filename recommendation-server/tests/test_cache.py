"""
test_cache.py — compute_input_hash 단위 테스트
Supabase 의존 함수(load_cache, save_cache_if_current, recompute_recommendations)는
통합 테스트이므로 이 파일에서 제외한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from engine.cache import (compute_input_hash, save_cache_if_current,
                          recompute_recommendations, _age_seconds,
                          rec_cache_reusable, STUCK_COMPUTING_SEC)


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

def _make_book(book_id: str, rating: str, has_feedback: bool = False) -> dict:
    return {"book_id": book_id, "rating": rating, "feedback_embedding": "x" if has_feedback else None}


class TestComputeInputHash:
    def test_same_input_same_hash(self):
        data = [_make_book("A", "good"), _make_book("B", "neutral")]
        assert compute_input_hash(data) == compute_input_hash(data)

    def test_order_independent(self):
        data1 = [_make_book("A", "good"), _make_book("B", "bad")]
        data2 = [_make_book("B", "bad"), _make_book("A", "good")]
        assert compute_input_hash(data1) == compute_input_hash(data2)

    def test_different_ratings_different_hash(self):
        data_good = [_make_book("A", "good")]
        data_bad = [_make_book("A", "bad")]
        assert compute_input_hash(data_good) != compute_input_hash(data_bad)

    def test_feedback_changes_hash(self):
        without_fb = [_make_book("A", "good", has_feedback=False)]
        with_fb = [_make_book("A", "good", has_feedback=True)]
        assert compute_input_hash(without_fb) != compute_input_hash(with_fb)

    def test_empty_data_returns_valid_hex(self):
        result = compute_input_hash([])
        assert len(result) == 64
        # valid hex chars only
        int(result, 16)

    def test_returns_64_char_hex(self):
        data = [_make_book("X", "neutral")]
        result = compute_input_hash(data)
        assert len(result) == 64
        int(result, 16)


# ---------------------------------------------------------------------------
# save_cache_if_current — stale-write 가드는 캐시 행이 아니라 *live DB 상태* 와
# 비교해야 한다. (과거: 캐시 행 hash 와 비교 → 좋아요 burst 중 racing recompute 가
# 남긴 stale 캐시 때문에 정답 결과가 거부돼 캐시가 stale hash 에 영구 고정되는 버그.
# 신규 Tier2 유저(온보딩 burst)의 /recommend 가 매번 ~8s 재계산.)
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._op = None
        self._row = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, row, **k):
        self._op = "upsert"
        self._row = row
        return self

    def execute(self):
        if self._op == "select":
            return _Result(list(self.store["tables"].get(self.table, [])))
        if self._op == "upsert":
            self.store["upserts"].append((self.table, self._row))
            return _Result([self._row])
        return _Result([])


class _FakeSB:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


def _goods(n):
    return [_make_book(f"B{i}", "good") for i in range(n)]


def _saved_cache_rows(store):
    return [r for (t, r) in store["upserts"] if t == "recommendation_cache"]


class TestSaveCacheIfCurrent:
    def test_saves_when_live_state_matches_even_if_cache_holds_stale_hash(self, monkeypatch):
        # 버그 재현: 캐시는 racing recompute 가 남긴 5권 hash 로 고정돼 있지만,
        # live user_books 는 6권이고 우리가 6권으로 정확히 계산했다 → 저장돼야 한다.
        six = _goods(6)
        live_hash = compute_input_hash(six)
        stale_hash = compute_input_hash(six[:5])
        store = {
            "tables": {
                "user_books": six,
                "recommendation_cache": [
                    {"input_hash": stale_hash, "computing": False,
                     "recommendations": [], "good_count": 5}
                ],
            },
            "upserts": [],
        }
        monkeypatch.setattr("engine.cache.get_supabase", lambda: _FakeSB(store))

        save_cache_if_current("u1", [{"book_id": "B0", "score": 1.0}],
                              live_hash, 6, 0, False)

        saved = _saved_cache_rows(store)
        assert saved, "live 상태와 일치하는 정답 결과는 stale 캐시가 있어도 저장돼야 한다"
        assert saved[-1]["input_hash"] == live_hash
        assert saved[-1]["good_count"] == 6

    def test_skips_when_live_state_moved_past_computed(self, monkeypatch):
        # 진짜 stale write 방지: 우리가 6권으로 계산하는 사이 유저가 7권째를 추가 →
        # live(7) != input(6) → 저장하면 안 된다.
        seven = _goods(7)
        store = {
            "tables": {"user_books": seven, "recommendation_cache": []},
            "upserts": [],
        }
        monkeypatch.setattr("engine.cache.get_supabase", lambda: _FakeSB(store))

        save_cache_if_current("u1", [{"book_id": "B0", "score": 1.0}],
                              compute_input_hash(seven[:6]), 6, 0, False)

        assert not _saved_cache_rows(store), "live 상태가 앞섰으면 stale write 를 skip 해야 한다"

    def test_saves_on_happy_path(self, monkeypatch):
        three = _goods(3)
        h = compute_input_hash(three)
        store = {
            "tables": {"user_books": three, "recommendation_cache": []},
            "upserts": [],
        }
        monkeypatch.setattr("engine.cache.get_supabase", lambda: _FakeSB(store))

        save_cache_if_current("u1", [{"book_id": "B0", "score": 1.0}], h, 3, 0, False)

        saved = _saved_cache_rows(store)
        assert saved and saved[-1]["input_hash"] == h
        assert saved[-1]["computing"] is False


# ---------------------------------------------------------------------------
# recompute_recommendations — stuck computing 데드락 가드
# computing 이 STUCK_COMPUTING_SEC 넘게 켜져 있으면(중단된 재계산) skip 하지 않고
# 재계산을 재시도해야 한다. (실측: 한 유저가 이틀간 computing=true 로 고정돼 모든
# 재계산이 skip → /home 이 매번 인라인 재계산 ~8~17s. computed_at 나이로 stuck 판정.)
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestRecomputeStuckGuard:
    def test_age_seconds_parses_and_infinite_on_garbage(self):
        assert _age_seconds("") == float("inf")
        assert _age_seconds("not-a-date") == float("inf")
        recent = _iso(datetime.now(timezone.utc))
        assert _age_seconds(recent) < 5

    def test_skips_when_computing_flag_is_fresh(self, monkeypatch):
        fresh = _iso(datetime.now(timezone.utc))
        store = {
            "tables": {"recommendation_cache": [
                {"computing": True, "computed_at": fresh, "recommendations": []}
            ]},
            "upserts": [],
        }
        monkeypatch.setattr("engine.cache.get_supabase", lambda: _FakeSB(store))
        # 신선한 computing → 가드에서 즉시 return, 아무 upsert(재계산 착수) 없어야 한다.
        recompute_recommendations("u1", app_state=None)
        assert store["upserts"] == [], "신선한 computing 은 중복 재계산 skip 이어야"

    def test_proceeds_when_computing_flag_is_stuck(self, monkeypatch):
        stale = "2020-01-01T00:00:00+00:00"  # STUCK_COMPUTING_SEC 훨씬 초과
        store = {
            "tables": {
                "recommendation_cache": [
                    {"computing": True, "computed_at": stale, "recommendations": []}
                ],
                "user_books": [],  # 착수 후 빈 데이터 분기로 조기 종료(무거운 스코어링 회피)
            },
            "upserts": [],
        }
        monkeypatch.setattr("engine.cache.get_supabase", lambda: _FakeSB(store))
        app_state = SimpleNamespace(prestacked_reasons=None)
        recompute_recommendations("u1", app_state)
        # stuck 이므로 skip 하지 않고 진행 → computing 플래그 세팅 + 빈 데이터 리셋 upsert.
        rows = _saved_cache_rows(store)
        assert rows, "stuck computing 은 재계산을 재시도(진행)해야 한다"
        assert rows[-1]["computing"] is False, "빈 데이터 분기에서 computing 을 해제해야"

    def test_stuck_threshold_is_positive(self):
        assert STUCK_COMPUTING_SEC > 0


# ---------------------------------------------------------------------------
# rec_cache_reusable — /home·/recommend 공통 재사용 판정.
# 핵심: computed_at > built_at 미충족(인덱스 재빌드 후 옛 계산본)이면 재사용 금지 →
# /home 이 재계산하게. (실측: Eden 추천이 인덱스 빌드 이전 계산본이라 stale 서빙되던 gap.)
# ---------------------------------------------------------------------------

class TestRecCacheReusable:
    HASH = "abc123"
    BUILT = "2026-06-29T09:50:00+00:00"
    FRESH = {"computing": False, "recommendations": [{"book_id": "b"}],
             "input_hash": HASH, "computed_at": "2026-06-30T00:00:00+00:00"}

    def test_reusable_when_fresh(self):
        assert rec_cache_reusable(self.FRESH, self.HASH, self.BUILT) is True

    def test_none_cache(self):
        assert rec_cache_reusable(None, self.HASH, self.BUILT) is False

    def test_computing(self):
        c = {**self.FRESH, "computing": True}
        assert rec_cache_reusable(c, self.HASH, self.BUILT) is False

    def test_no_recommendations(self):
        c = {**self.FRESH, "recommendations": []}
        assert rec_cache_reusable(c, self.HASH, self.BUILT) is False

    def test_hash_mismatch(self):
        assert rec_cache_reusable(self.FRESH, "other", self.BUILT) is False

    def test_computed_before_index_build_not_reusable(self):
        # 캐시가 인덱스 빌드 이전 계산본 → 재사용 금지(재계산 유도). 이게 이번 핵심 수정.
        stale = {**self.FRESH, "computed_at": "2026-06-29T01:34:00+00:00"}
        assert rec_cache_reusable(stale, self.HASH, self.BUILT) is False
