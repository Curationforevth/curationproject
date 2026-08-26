"""v3_reason_extract 스킵 판정 — '임베딩된' reason 이 있어야 처리 완료다.

2026-08-26 실측으로 드러난 결함: 스킵 판정이
    .select("book_id").eq("source", SOURCE_TAG)
로 **행 존재만** 봤다. 2026-04-01~12 에 임베딩 없이 저장된 v3 reason 8,991건 때문에
**1,240권이 임베딩된 reason 을 하나도 못 가진 채 '처리 완료'로 분류**돼, 재실행해도
영원히 복구되지 않았다(추천의 reason 축에서 통째로 누락). 임베딩 실패는 조용히
영구화되면 안 된다 — 다음 run 이 다시 집어가야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class _FakeQuery:
    """supabase-py 체이닝을 흉내내며 어떤 필터가 걸렸는지 기록한다."""

    def __init__(self, recorder, rows):
        self.rec = recorder
        self.rows = rows

    def select(self, *a, **kw):
        self.rec["select"] = a
        return self

    def eq(self, col, val):
        self.rec.setdefault("eq", []).append((col, val))
        return self

    def is_(self, col, val):
        self.rec.setdefault("is_", []).append((col, val))
        return self

    @property
    def not_(self):
        self.rec["not_"] = True
        return self

    def range(self, a, b):
        self.rec["range"] = (a, b)
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.rows
        return r


class _FakeSb:
    def __init__(self, rows):
        self.rec = {}
        self.rows = rows

    def table(self, name):
        self.rec["table"] = name
        return _FakeQuery(self.rec, self.rows)


def test_done_ids_requires_non_null_embedding():
    """임베딩이 NULL 인 행만 가진 책은 '완료'로 보면 안 된다."""
    import v3_reason_extract as mod

    sb = _FakeSb([{"book_id": "b1"}])
    done = mod.fetch_done_book_ids(sb)

    assert done == {"b1"}
    assert sb.rec.get("not_") is True, (
        "reason_embedding IS NOT NULL 필터가 없으면 임베딩 실패가 영구화된다")
    assert ("reason_embedding", "null") in sb.rec.get("is_", []), (
        "reason_embedding 에 대한 null 필터가 걸려야 한다")
    assert ("source", mod.SOURCE_TAG) in sb.rec.get("eq", [])


def test_done_ids_empty_when_no_rows():
    import v3_reason_extract as mod
    sb = _FakeSb([])
    assert mod.fetch_done_book_ids(sb) == set()
