"""books_upsert — 생성컬럼(l1/l2) 제외 + richer merge 테스트.

l1/l2 는 books.genre 에서 GENERATED ALWAYS AS ... STORED 로 자동 계산되는 컬럼
(migration 20260415000008). 절대 insert/upsert 불가 →
upsert payload 에 실리면 postgrest APIError 428C9 로 청크 전체 실패한다.
"""
import sys
import os
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class _FakeQuery:
    """sb.table("books").select/in_/upsert(...).execute() 체인 캡처."""

    def __init__(self, fake):
        self.fake = fake
        self._op = None
        self._isbns = None
        self._payload = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def in_(self, col, vals):
        self._isbns = vals
        return self

    def upsert(self, rows, on_conflict=None):
        self._op = "upsert"
        self._payload = rows
        self.fake.upserted.extend(rows)
        return self

    def execute(self):
        if self._op == "select":
            data = [r for r in self.fake.existing
                    if r.get("isbn") in (self._isbns or [])]
            return SimpleNamespace(data=data)
        return SimpleNamespace(data=self._payload)


class FakeSupabase:
    def __init__(self, existing):
        self.existing = existing
        self.upserted = []

    def table(self, name):
        return _FakeQuery(self)


def test_upsert_excludes_generated_l1_l2_on_merge():
    """기존 row(SELECT * 로 l1/l2 포함) 와 merge 후에도 l1/l2 는 upsert 에서 제외."""
    from lib.books_upsert import upsert_books_rich_merge

    existing = [{
        "id": "uuid-1", "isbn": "9788900000001",
        "title": "old", "genre": "문학>소설",
        "l1": "문학", "l2": "소설",          # ← SELECT * 가 끌어오는 생성컬럼
        "loan_count": 5,
    }]
    sb = FakeSupabase(existing)
    new = [{"isbn": "9788900000001", "title": "a much longer title",
            "genre": "문학>소설", "loan_count": 10}]

    upsert_books_rich_merge(sb, new)

    assert len(sb.upserted) == 1
    payload = sb.upserted[0]
    assert "l1" not in payload, f"l1(생성컬럼)이 upsert payload에 있음: {payload}"
    assert "l2" not in payload, f"l2(생성컬럼)이 upsert payload에 있음: {payload}"
    # merge 자체는 정상 — 더 긴 title 채택, 더 큰 loan_count 채택, genre 유지
    assert payload["title"] == "a much longer title"
    assert payload["loan_count"] == 10
    assert payload["genre"] == "문학>소설"
