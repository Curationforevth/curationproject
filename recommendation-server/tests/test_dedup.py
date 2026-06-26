"""test_dedup.py — 작품 단위 dedup 유틸 단위 테스트.

같은 작품(다른 판본/ISBN → 다른 UUID)이 추천/유사 목록에 중복 노출되는 것을
serving 레이어에서 제거한다. 인덱스의 0.8%(28그룹)가 중복 작품(동물농장 3판본 등).
"""
from __future__ import annotations

from engine.dedup import work_key, dedup_by_work, dedup_similar


class TestWorkKey:
    def test_same_work_different_edition_collapses(self):
        # 같은 작품의 다른 판본 — 부제/괄호 표기만 다름 → 같은 키
        a = work_key("싯다르타 (오리지널 초판본 표지 디자인)", "헤르만 헤세 (지은이)")
        b = work_key("싯다르타", "헤르만 헤세 (지은이), 강영옥 (옮긴이)")
        assert a == b

    def test_subtitle_after_colon_ignored(self):
        a = work_key("자유론 : 무삭제 완역본", "존 스튜어트 밀")
        b = work_key("자유론", "존 스튜어트 밀 (지은이)")
        assert a == b

    def test_different_works_distinct(self):
        assert work_key("동물농장", "조지 오웰") != work_key("1984", "조지 오웰")

    def test_same_title_different_author_distinct(self):
        # 같은 제목 다른 저자는 다른 작품으로 본다
        assert work_key("변신", "카프카") != work_key("변신", "이상")

    def test_empty_title_is_unique(self):
        # 제목 없으면 절대 묶이면 안 된다(빈 키로 뭉뚱그리기 방지)
        assert work_key("", "anon") is None


class TestDedupByWork:
    def test_keeps_first_occurrence(self):
        items = [
            {"book_id": "u1", "title": "동물농장", "author": "조지 오웰 (지은이)"},
            {"book_id": "u2", "title": "1984", "author": "조지 오웰"},
            {"book_id": "u3", "title": "동물농장 (세트)", "author": "조지 오웰 (지은이), 도정일"},
        ]
        out = dedup_by_work(items, lambda it: (it["title"], it["author"]))
        ids = [it["book_id"] for it in out]
        assert ids == ["u1", "u2"]  # u3 는 u1 과 같은 작품 → 제거, 순서 보존

    def test_no_dupes_passthrough(self):
        items = [
            {"book_id": "a", "title": "변신", "author": "카프카"},
            {"book_id": "b", "title": "변신", "author": "이상"},
        ]
        out = dedup_by_work(items, lambda it: (it["title"], it["author"]))
        assert [it["book_id"] for it in out] == ["a", "b"]

    def test_empty_titles_never_collapse(self):
        items = [
            {"book_id": "a", "title": "", "author": "x"},
            {"book_id": "b", "title": "", "author": "x"},
        ]
        out = dedup_by_work(items, lambda it: (it["title"], it["author"]))
        assert [it["book_id"] for it in out] == ["a", "b"]


class TestDedupSimilar:
    META = {
        "seed": {"title": "동물농장", "author": "조지 오웰 (지은이)"},
        "seed_other_ed": {"title": "동물농장 (세트)", "author": "조지 오웰"},
        "b1": {"title": "1984", "author": "조지 오웰"},
        "b1_dup": {"title": "1984 (초판본)", "author": "조지 오웰 (지은이)"},
        "b2": {"title": "멋진 신세계", "author": "올더스 헉슬리"},
    }

    def test_excludes_seed_other_editions_and_dedups(self):
        raw = [("seed_other_ed", 0.9), ("b1", 0.8), ("b1_dup", 0.7), ("b2", 0.6)]
        out = dedup_similar(raw, self.META, "seed", limit=10)
        ids = [bid for bid, _ in out]
        assert "seed_other_ed" not in ids          # 시드의 다른 판본 제외
        assert ids == ["b1", "b2"]                  # b1_dup 은 b1 과 같은 작품 → 제거

    def test_truncates_to_limit_after_dedup(self):
        raw = [("b1", 0.8), ("b1_dup", 0.7), ("b2", 0.6)]
        out = dedup_similar(raw, self.META, "seed", limit=1)
        assert [bid for bid, _ in out] == ["b1"]
