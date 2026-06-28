"""C1/C2 헬퍼 — 텍스트 우선순위, embed-once, 인덱스 밖 책 벡터 resolve."""
import numpy as np

from engine.user_embed import (_pick_source_text, ensure_books_embedded,
                               resolve_extra_query_vectors,
                               build_feedback_text, ensure_feedback_embedded)


# --------------------------------------------------------------------------
# Fake Supabase (supabase-py 체이닝 흉내)
# --------------------------------------------------------------------------
class _Res:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self._cols = None
        self._in_ids = None
        self._upsert = None
        self._update = None
        self._eq = {}

    def select(self, cols):
        self._cols = cols
        return self

    def in_(self, col, ids):
        self._in_ids = list(ids)
        return self

    def upsert(self, row, on_conflict=None):
        self._upsert = row
        return self

    def update(self, row):
        self._update = row
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def execute(self):
        if self._update is not None:
            key = (self._eq.get("user_id"), self._eq.get("book_id"))
            self.sb.user_books_updates[key] = self._update
            return _Res([self._update])
        if self._upsert is not None:
            self.sb.v3[self._upsert["book_id"]] = self._upsert
            return _Res([self._upsert])
        if self.table == "book_v3_vectors":
            rows = []
            for bid in (self._in_ids or []):
                if bid in self.sb.v3:
                    full = self.sb.v3[bid]
                    cols = (self._cols or "").split(",")
                    rows.append({c: full.get(c) for c in cols})
            return _Res(rows)
        if self.table == "books":
            return _Res([dict(self.sb.books[b], id=b)
                         for b in (self._in_ids or []) if b in self.sb.books])
        return _Res([])


class _FakeSB:
    def __init__(self, books=None, v3=None):
        self.books = books or {}
        self.v3 = v3 or {}
        self.user_books_updates = {}

    def table(self, name):
        return _FakeQuery(self, name)


# --------------------------------------------------------------------------
# _pick_source_text
# --------------------------------------------------------------------------
def test_pick_source_prefers_rich_when_long():
    row = {"rich_description": "가" * 250, "description": "짧은",
           "title": "T", "author": "A", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert text == "가" * 250 and prov is False


def test_pick_source_falls_back_to_description():
    row = {"rich_description": "짧음", "description": "카카오 줄거리 문단입니다.",
           "title": "T", "author": "A", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert text == "카카오 줄거리 문단입니다." and prov is True


def test_pick_source_last_resort_title_author_genre():
    row = {"rich_description": None, "description": None,
           "title": "어린왕자", "author": "생텍쥐페리", "genre": "소설"}
    text, prov = _pick_source_text(row)
    assert "어린왕자" in text and "생텍쥐페리" in text and "소설" in text and prov is True


# --------------------------------------------------------------------------
# ensure_books_embedded — embed-once
# --------------------------------------------------------------------------
def test_ensure_books_embedded_skips_already_present():
    calls = []
    sb = _FakeSB(books={"B1": {"rich_description": "가" * 250}},
                 v3={"B1": {"book_id": "B1"}})  # B1 이미 임베딩됨
    ensure_books_embedded(["B1"], sb, embed_fn=lambda t: calls.append(t) or [0.0] * 2000)
    assert calls == [], "이미 임베딩된 책은 OpenAI 호출 0회(embed-once)"


def test_ensure_books_embedded_embeds_missing_with_provisional():
    calls = []
    sb = _FakeSB(books={"B2": {"description": "카카오 줄거리", "title": "t", "author": "a", "genre": "g"}},
                 v3={})
    ensure_books_embedded(["B2"], sb, embed_fn=lambda t: calls.append(t) or [0.1] * 2000)
    assert calls == ["카카오 줄거리"], "미임베딩 책은 가용 텍스트로 1회 임베딩"
    assert sb.v3["B2"]["provisional"] is True, "얕은 텍스트 → provisional"
    assert sb.v3["B2"]["desc_embedding"] == [0.1] * 2000


def test_ensure_books_embedded_per_book_isolation():
    """한 책 임베딩 실패가 다른 책을 막지 않는다."""
    def flaky(t):
        if "bad" in t:
            raise RuntimeError("openai down")
        return [0.2] * 2000
    sb = _FakeSB(books={"OK": {"description": "good text"}, "BAD": {"description": "bad text"}}, v3={})
    ensure_books_embedded(["OK", "BAD"], sb, embed_fn=flaky)
    assert "OK" in sb.v3 and "BAD" not in sb.v3


# --------------------------------------------------------------------------
# resolve_extra_query_vectors
# --------------------------------------------------------------------------
def test_resolve_only_returns_out_of_index_books():
    vec = [0.0] * 1999 + [1.0]
    sb = _FakeSB(v3={"OUT": {"book_id": "OUT", "desc_embedding": vec}})
    out = resolve_extra_query_vectors(["IN_INDEX", "OUT"], {"IN_INDEX"}, sb)
    assert set(out.keys()) == {"OUT"}, "인덱스 밖 책만 resolve"
    bv = out["OUT"]
    assert bv.desc.shape == (2000,) and bv.reasons == []
    assert np.allclose(bv.l1, 0) and np.allclose(bv.l2, 0)


def test_resolve_empty_when_all_in_index():
    sb = _FakeSB(v3={})
    assert resolve_extra_query_vectors(["A", "B"], {"A", "B"}, sb) == {}


# --------------------------------------------------------------------------
# C3 — build_feedback_text / ensure_feedback_embedded
# --------------------------------------------------------------------------
def test_build_feedback_text_full_review_no_truncation():
    review = "이 책은 " + "정말 " * 50 + "좋았다"   # 200자+
    out = build_feedback_text(["문체", "분위기"], review)
    assert out.startswith("태그: 문체, 분위기\n")
    assert review in out  # 절단 금지
    assert "..." not in out and "리뷰:" not in out


def test_build_feedback_text_tags_only():
    assert build_feedback_text(["성장"], None) == "태그: 성장"


def test_build_feedback_text_review_only():
    assert build_feedback_text(None, "좋음") == "좋음"


def test_build_feedback_text_none_when_empty():
    assert build_feedback_text(None, None) is None
    assert build_feedback_text([], "  ") is None


def test_ensure_feedback_embedded_embeds_tags_only_row():
    calls = []
    sb = _FakeSB()
    rows = [{"user_id": "U", "book_id": "B", "emotion_tags": ["문체"],
             "review_text": None, "feedback_embedding": None}]
    ensure_feedback_embedded(rows, sb, embed_fn=lambda t: calls.append(t) or [0.3] * 2000)
    assert calls == ["태그: 문체"], "리뷰 없어도 태그만으로 임베딩"
    assert rows[0]["feedback_embedding"] == [0.3] * 2000  # in-place 갱신
    assert sb.user_books_updates[("U", "B")]["feedback_embedding"] == [0.3] * 2000


def test_ensure_feedback_embedded_skips_already_embedded():
    calls = []
    rows = [{"user_id": "U", "book_id": "B", "emotion_tags": ["문체"],
             "review_text": "좋음", "feedback_embedding": [0.9] * 2000}]
    ensure_feedback_embedded(rows, _FakeSB(), embed_fn=lambda t: calls.append(t) or [0.0] * 2000)
    assert calls == [], "이미 임베딩된 행은 skip"
