"""PR-C: recompute DB 왕복 축소 — I/O 계약 테스트.

계약(2026-07-02 계측: total 2.76s 중 I/O ~1.4s/5왕복):
  1. user_books SELECT 는 1회만(재read 제거) — in-place 갱신 행이 곧 스코어링 입력.
  2. ensure_feedback_embedded 의 in-place fb 갱신이 input_hash 에 반영(코히런스).
  3. ensure_books_embedded 는 인덱스 밖 rated 책만 받는다(인덱스 내 책 = 빌드 시
     book_v3_vectors 존재 보장 → 전부 인덱스 내면 빈 리스트 = DB 콜 0).
  4. computing 플래그: 기존 행은 UPDATE(recommendations 미전송=보존), 신규만 upsert.
  5. save_cache_if_current: live hash 불일치 시 저장 skip + computing 해제
     (안 내리면 다음 트리거가 STUCK 180s 가드까지 억제되는 잠재 데드락).
"""
import types

import numpy as np

from engine import cache as cache_mod
from engine.index import BookVectors, VectorIndex


def _unit(seed, dim=8):
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
        self._op = None
        self._payload = None

    def upsert(self, row=None, *a, **k):
        self._op = "upsert"
        self._payload = row
        return self

    def update(self, row=None, *a, **k):
        self._op = "update"
        self._payload = row
        return self

    def select(self, *a, **k):
        self._op = "select"
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        self.sb.ops.append((self.table, self._op, self._payload))
        if self.table == "user_books" and self._op == "select":
            self.sb.user_books_selects += 1
            return _StubExec([r for r in self.sb.user_books])  # 같은 dict 참조(공유) — in-place 관찰
        return _StubExec([])


class _StubSB:
    def __init__(self, user_books):
        self.user_books = user_books
        self.user_books_selects = 0
        self.ops = []

    def table(self, name):
        return _StubQuery(name, self)


def _app_state(dim=8):
    idx = VectorIndex(dim=dim, dtype=np.float16)
    idx.add_book("cand", reasons=[], desc=_unit(1, dim),
                 l1=np.zeros(dim), l2=np.zeros(dim))
    idx.build_desc_matrix()
    return types.SimpleNamespace(
        index=idx,
        prestacked_reasons={},
        bid_order=["cand", "in_index_book"],
        desc_matrix_f16=np.stack([_unit(1, dim), _unit(2, dim)]).astype(np.float16),
        agg_reason_matrix_f16=np.zeros((2, dim), np.float16),
        books_meta={"cand": {"title": "후보", "author": "", "cover_url": None}},
        built_at="2000-01-01",
    )


def _patch_embeds(monkeypatch, fb_side_effect=None):
    def fake_fb(rows, sb=None, **k):
        if fb_side_effect:
            fb_side_effect(rows)
    calls = {}

    def fake_books(book_ids, sb=None, **k):
        calls["book_ids"] = list(book_ids)
    monkeypatch.setattr("engine.user_embed.ensure_feedback_embedded", fake_fb)
    monkeypatch.setattr("engine.user_embed.ensure_books_embedded", fake_books)
    monkeypatch.setattr("engine.user_embed.resolve_extra_query_vectors",
                        lambda *a, **k: {})
    return calls


def test_user_books_read_only_once(monkeypatch):
    sb = _StubSB([{"book_id": "in_index_book", "rating": "good",
                   "feedback_embedding": None, "emotion_tags": None, "review_text": None}])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)
    _patch_embeds(monkeypatch)
    monkeypatch.setattr(cache_mod, "save_cache_if_current", lambda *a, **k: None)

    cache_mod.recompute_recommendations("U1", _app_state())

    assert sb.user_books_selects == 1, "user_books 재read(db2) 는 제거돼야 한다"


def test_inplace_fb_update_reflected_in_hash(monkeypatch):
    """ensure_feedback_embedded 가 심은 fb 가 input_hash(has_fb=1)에 반영돼야 한다."""
    row = {"book_id": "in_index_book", "rating": "good",
           "feedback_embedding": None, "emotion_tags": ["몰입"], "review_text": "좋다"}
    sb = _StubSB([row])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)

    def seed_fb(rows):
        for r in rows:
            r["feedback_embedding"] = list(_unit(3).astype(float))
    _patch_embeds(monkeypatch, fb_side_effect=seed_fb)

    captured = {}
    monkeypatch.setattr(
        cache_mod, "save_cache_if_current",
        lambda uid, recs, input_hash, *a, **k: captured.update(h=input_hash))

    cache_mod.recompute_recommendations("U1", _app_state())

    expected = cache_mod.compute_input_hash(
        [{"book_id": "in_index_book", "rating": "good", "feedback_embedding": "x"}])
    assert captured["h"] == expected, "hash 는 in-place 갱신(has_fb=1) 상태여야 한다"


def test_ensure_books_embedded_only_out_of_index(monkeypatch):
    sb = _StubSB([
        {"book_id": "in_index_book", "rating": "good", "feedback_embedding": None,
         "emotion_tags": None, "review_text": None},
        {"book_id": "OUTSIDE", "rating": "good", "feedback_embedding": None,
         "emotion_tags": None, "review_text": None},
        {"book_id": "neutral_outside", "rating": "neutral", "feedback_embedding": None,
         "emotion_tags": None, "review_text": None},
    ])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)
    calls = _patch_embeds(monkeypatch)
    monkeypatch.setattr(cache_mod, "save_cache_if_current", lambda *a, **k: None)

    cache_mod.recompute_recommendations("U1", _app_state())

    assert calls["book_ids"] == ["OUTSIDE"], \
        "인덱스 내(빌드시 벡터 보장)·neutral 은 제외, 인덱스 밖 rated 만 전달"


def test_computing_flag_update_for_existing_row(monkeypatch):
    """기존 행: UPDATE(recommendations 미전송 = 보존). upsert 로 recs 를 되보내지 않는다."""
    sb = _StubSB([{"book_id": "in_index_book", "rating": "good",
                   "feedback_embedding": None, "emotion_tags": None, "review_text": None}])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    prior = [{"book_id": "old", "score": 1.0}]
    monkeypatch.setattr(cache_mod, "load_cache",
                        lambda uid: {"computing": False, "recommendations": prior})
    _patch_embeds(monkeypatch)
    monkeypatch.setattr(cache_mod, "save_cache_if_current", lambda *a, **k: None)

    cache_mod.recompute_recommendations("U1", _app_state())

    flag_ops = [(t, op, p) for t, op, p in sb.ops
                if t == "recommendation_cache" and op in ("update", "upsert")
                and p and p.get("computing") is True]
    assert flag_ops, "computing 플래그 write 가 있어야 함"
    t, op, payload = flag_ops[0]
    assert op == "update", "기존 행은 UPDATE 로 플래그만 갱신"
    assert "recommendations" not in payload, "recs 미전송(미접촉 = 보존)"


def test_computing_flag_upsert_for_new_row(monkeypatch):
    """캐시 행이 없으면 upsert 로 생성(computing=true, recs=[])."""
    sb = _StubSB([{"book_id": "in_index_book", "rating": "good",
                   "feedback_embedding": None, "emotion_tags": None, "review_text": None}])
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    monkeypatch.setattr(cache_mod, "load_cache", lambda uid: None)
    _patch_embeds(monkeypatch)
    monkeypatch.setattr(cache_mod, "save_cache_if_current", lambda *a, **k: None)

    cache_mod.recompute_recommendations("U1", _app_state())

    flag_ops = [(op, p) for t, op, p in sb.ops
                if t == "recommendation_cache" and p and p.get("computing") is True]
    assert flag_ops and flag_ops[0][0] == "upsert"
    assert flag_ops[0][1].get("recommendations") == []


class _LiveMovedSB(_StubSB):
    """save_cache_if_current 의 live 체크가 '다른 상태'를 보게 하는 스텁."""
    def __init__(self):
        super().__init__([{"book_id": "b1", "rating": "good", "feedback_embedding": None}])


def test_save_skip_clears_computing(monkeypatch):
    """live 가 계산 시점보다 앞섰으면 저장 skip + computing 해제(데드락 방지)."""
    sb = _LiveMovedSB()
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)

    stale_hash = cache_mod.compute_input_hash(
        [{"book_id": "b1", "rating": "bad", "feedback_embedding": None}])  # live 와 다름
    cache_mod.save_cache_if_current("U1", [], stale_hash, 1, 0, False)

    saves = [(op, p) for t, op, p in sb.ops if t == "recommendation_cache"]
    assert not any(p and p.get("recommendations") is not None and op == "upsert"
                   for op, p in saves), "stale 결과는 저장하지 않는다"
    assert any(op == "update" and p == {"computing": False} for op, p in saves), \
        "skip 시 computing 을 내려 다음 트리거가 즉시 재계산하게 한다"


def test_save_proceeds_when_live_matches(monkeypatch):
    sb = _LiveMovedSB()
    monkeypatch.setattr(cache_mod, "get_supabase", lambda: sb)
    live_hash = cache_mod.compute_input_hash(
        [{"book_id": "b1", "rating": "good", "feedback_embedding": None}])
    cache_mod.save_cache_if_current("U1", [{"book_id": "cand", "score": 1.0}],
                                    live_hash, 1, 0, False)
    assert any(op == "upsert" and p and p.get("computing") is False
               for t, op, p in sb.ops if t == "recommendation_cache"), "정상 저장"
