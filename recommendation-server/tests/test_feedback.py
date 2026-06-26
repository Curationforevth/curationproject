"""test_feedback.py — /feedback 백그라운드 임베딩+재계산 로직.

요청경로에서 OpenAI 동기호출을 제거(비차단)하고, 임베딩→user_books 갱신→recompute
순서를 백그라운드에서 보장한다. 임베딩 실패 시 feedback_embedding 은 null 로 남고
backfill 배치가 채운다(신호 소실 0).
"""
from __future__ import annotations

import api.feedback as fb


class _FakeTbl:
    def __init__(self, rec):
        self.rec = rec

    def update(self, d):
        self.rec["updates"].append(d)
        return self

    def eq(self, *a):
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class _FakeSB:
    def __init__(self, rec):
        self.rec = rec

    def table(self, name):
        return _FakeTbl(self.rec)


def _state(index):
    return type("S", (), {"index": index})()


def _wire(monkeypatch, rec, embed):
    monkeypatch.setattr(fb, "get_supabase", lambda: _FakeSB(rec))
    monkeypatch.setattr(fb, "_embed_text", embed)
    monkeypatch.setattr(fb, "recompute_recommendations",
                        lambda u, s: rec["recompute"].append(u))


class TestEmbedAndRecompute:
    def test_embed_success_updates_then_recomputes(self, monkeypatch):
        rec = {"updates": [], "recompute": []}
        _wire(monkeypatch, rec, lambda t: [0.1, 0.2])
        fb._embed_and_recompute("u1", "b1", "잔잔한 문체가 좋았다", _state(object()))
        assert rec["updates"] and rec["updates"][0]["feedback_embedding"] == [0.1, 0.2]
        assert rec["recompute"] == ["u1"]

    def test_embed_failure_keeps_null_but_still_recomputes(self, monkeypatch):
        rec = {"updates": [], "recompute": []}

        def boom(_):
            raise RuntimeError("openai down")
        _wire(monkeypatch, rec, boom)
        fb._embed_and_recompute("u1", "b1", "좋았다", _state(object()))
        assert rec["updates"] == []        # 실패 → null 유지(backfill 이 채움), 크래시 X
        assert rec["recompute"] == ["u1"]   # 재계산(평점 신호)은 그대로 진행

    def test_no_review_skips_embedding(self, monkeypatch):
        rec = {"updates": [], "recompute": []}
        called = []
        _wire(monkeypatch, rec, lambda t: called.append(t) or [0.1])
        fb._embed_and_recompute("u1", "b1", None, _state(object()))
        assert called == [] and rec["updates"] == []
        assert rec["recompute"] == ["u1"]

    def test_blank_review_skips_embedding(self, monkeypatch):
        rec = {"updates": [], "recompute": []}
        called = []
        _wire(monkeypatch, rec, lambda t: called.append(t) or [0.1])
        fb._embed_and_recompute("u1", "b1", "   ", _state(object()))
        assert called == [] and rec["updates"] == []

    def test_index_none_skips_recompute(self, monkeypatch):
        rec = {"updates": [], "recompute": []}
        _wire(monkeypatch, rec, lambda t: [0.1])
        fb._embed_and_recompute("u1", "b1", "x", _state(None))
        assert rec["recompute"] == []      # 인덱스 미로드면 재계산 skip
