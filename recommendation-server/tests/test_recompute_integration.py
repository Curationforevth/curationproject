"""Task 5 통합 — recompute_recommendations 가 인덱스 밖 좋아요 책을
embed→resolve→augment 경로로 추천에 반영하는지."""
import types

import numpy as np

from engine import cache as cache_mod
from engine.index import BookVectors, VectorIndex


def _unit(seed, dim=2000):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


class _StubExec:
    def __init__(self, data):
        self.data = data


class _StubQuery:
    def __init__(self, table, sb):
        self.table = table
        self.sb = sb

    def upsert(self, row=None, *a, **k):
        if self.table == "recommendation_cache" and row is not None:
            self.sb.cache_upserts.append(row)
        return self

    def update(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        if self.table == "user_books":
            return _StubExec([dict(r) for r in self.sb.user_books])
        return _StubExec([])


class _StubSB:
    def __init__(self, user_books):
        self.user_books = user_books
        self.cache_upserts = []

    def table(self, name):
        return _StubQuery(name, self)


def test_recompute_reflects_out_of_index_liked_book(monkeypatch):
    dim = 2000
    # 후보 인덱스: cand 하나(= USER_BOOK desc 와 유사)
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim),
                 l1=np.zeros(dim), l2=np.zeros(dim))
    idx.build_desc_matrix()
    app_state = types.SimpleNamespace(
        index=idx,
        prestacked_reasons={},           # not None → v4 prestacked 경로
        bid_order=["cand"],
        desc_matrix_f16=np.stack([_unit(1, dim)]).astype(np.float16),
        agg_reason_matrix_f16=np.zeros((1, dim), np.float16),
        books_meta={"cand": {"title": "후보책", "author": "저자", "cover_url": None}},
        built_at="2000-01-01",
    )

    # user_books: USER_BOOK(good, 인덱스 밖)
    sb = _StubSB([{"book_id": "USER_BOOK", "rating": "good", "feedback_embedding": None,
                   "emotion_tags": None, "review_text": None}])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)
    # 임베딩은 no-op(이미 임베딩됐다고 가정), resolve 가 USER_BOOK 벡터를 줌
    monkeypatch.setattr("engine.user_embed.ensure_feedback_embedded", lambda *a, **k: None)
    monkeypatch.setattr("engine.user_embed.ensure_books_embedded", lambda *a, **k: None)
    monkeypatch.setattr(
        "engine.user_embed.resolve_extra_query_vectors",
        lambda ids, s, sb=None: {"USER_BOOK": BookVectors(
            reasons=[], desc=_unit(1, dim),
            l1=np.zeros(dim, np.float32), l2=np.zeros(dim, np.float32))})

    saved = {}
    monkeypatch.setattr(cache_mod, "save_cache_if_current",
                        lambda uid, recs, *a, **k: saved.update(recs=recs))

    cache_mod.recompute_recommendations("U1", app_state)

    assert saved.get("recs"), "인덱스 밖 좋아요 책 기반 추천이 생성돼야 함"
    assert saved["recs"][0]["book_id"] == "cand"


def test_recompute_empty_user_clears_cache(monkeypatch):
    """좋아요 0 → 빈 캐시 초기화(회귀)."""
    app_state = types.SimpleNamespace(
        index=VectorIndex(dim=8), prestacked_reasons={}, bid_order=[],
        desc_matrix_f16=np.zeros((0, 8), np.float16),
        agg_reason_matrix_f16=np.zeros((0, 8), np.float16),
        books_meta={}, built_at="2000-01-01")
    sb = _StubSB([])  # no user_books
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)
    saved = {}
    monkeypatch.setattr(cache_mod, "save_cache_if_current",
                        lambda *a, **k: saved.update(called=True))
    cache_mod.recompute_recommendations("U2", app_state)
    assert "called" not in saved  # 빈 유저는 save_cache_if_current 안 탐(자체 upsert)


def test_computing_flag_preserves_existing_recommendations(monkeypatch):
    """§4.5 R2 NEW#1: computing=True 세팅 시 기존 recs 를 비우지 않는다(stale-serve 보존)."""
    dim = 8
    app_state = types.SimpleNamespace(
        index=VectorIndex(dim=dim), prestacked_reasons={}, bid_order=["x"],
        desc_matrix_f16=np.zeros((1, dim), np.float16),
        agg_reason_matrix_f16=np.zeros((1, dim), np.float16),
        books_meta={}, built_at="2000-01-01")
    prior = [{"book_id": "old", "score": 1.0, "title": "이전추천", "author": "", "cover_url": None}]
    sb = _StubSB([{"book_id": "x", "rating": "good", "feedback_embedding": None,
                   "emotion_tags": None, "review_text": None}])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache",
                        lambda uid: {"computing": False, "recommendations": prior})
    monkeypatch.setattr("engine.user_embed.ensure_feedback_embedded", lambda *a, **k: None)
    monkeypatch.setattr("engine.user_embed.ensure_books_embedded", lambda *a, **k: None)
    monkeypatch.setattr("engine.user_embed.resolve_extra_query_vectors",
                        lambda *a, **k: {})
    monkeypatch.setattr(cache_mod, "save_cache_if_current", lambda *a, **k: None)

    cache_mod.recompute_recommendations("U3", app_state)

    # 첫 recommendation_cache upsert = computing 플래그 → recommendations 보존돼야 함
    assert sb.cache_upserts, "computing 플래그 upsert 가 있어야 함"
    flag_upsert = sb.cache_upserts[0]
    assert flag_upsert.get("computing") is True
    assert flag_upsert.get("recommendations") == prior, "기존 recs 가 보존돼야 함(blank 금지)"
