"""VectorIndex.similar_by_vector — 임의 query 벡터로 lookup."""
import numpy as np
import pytest
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def small_index():
    idx = VectorIndex(dim=8)
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    sci_l1 = _norm([0, 0, 1, 0, 0, 0, 0, 0])
    novel_l2a = _norm([1, 0, 0, 0, 0.3, 0, 0, 0])
    novel_l2b = _norm([1, 0, 0, 0, 0, 0.3, 0, 0])
    econ_l2 = _norm([0, 1, 0, 0, 0, 0, 0.3, 0])
    sci_l2 = _norm([0, 0, 1, 0, 0, 0, 0, 0.3])
    idx.add_book("novel1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                 l1=novel_l1, l2=novel_l2a, reasons=[])
    idx.add_book("novel2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l2b, reasons=[])
    idx.add_book("econ1", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l2, reasons=[])
    idx.add_book("econ2", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l2, reasons=[])
    idx.add_book("sci1", desc=_norm([0, 0, 1, 0, 0, 0, 0, 0.5]),
                 l1=sci_l1, l2=sci_l2, reasons=[])
    idx.build_desc_matrix()
    return idx


def test_similar_by_vector_returns_top_k_excluding_ids(small_index):
    nov1 = small_index.get_book("novel1").desc
    nov2 = small_index.get_book("novel2").desc
    avg = (nov1 + nov2) / 2
    avg = avg / np.linalg.norm(avg)
    results = small_index.similar_by_vector(avg, exclude_ids={"novel1", "novel2"}, limit=3)
    assert len(results) == 3
    ids = [r[0] for r in results]
    assert "novel1" not in ids
    assert "novel2" not in ids
    for _, score in results:
        assert -1.0 <= score <= 1.0


def test_similar_by_vector_orders_by_descending_score(small_index):
    nov1 = small_index.get_book("novel1").desc
    results = small_index.similar_by_vector(nov1, exclude_ids=set(), limit=5)
    scores = [r[1] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0][0] == "novel1"


def test_similar_by_vector_handles_empty_exclude(small_index):
    econ1 = small_index.get_book("econ1").desc
    results = small_index.similar_by_vector(econ1, exclude_ids=set(), limit=2)
    assert len(results) == 2


def test_similar_by_vector_respects_limit(small_index):
    nov1 = small_index.get_book("novel1").desc
    results = small_index.similar_by_vector(nov1, exclude_ids=set(), limit=1)
    assert len(results) == 1


# --------------------------------------------------------------------------
# source_tier down-weight + minimal 제외 (C4 / M1)
# --------------------------------------------------------------------------
def _tier_index():
    idx = VectorIndex(dim=4)
    q = _norm([1, 0, 0, 0])
    for bid in ("rich", "kdesc", "min"):
        idx.add_book(bid, reasons=[], desc=q,
                     l1=np.zeros(4, np.float32), l2=np.zeros(4, np.float32))
    idx._candidate_tier = {"kdesc": "kakao_desc", "min": "minimal"}
    idx.build_desc_matrix()
    return idx, q


def test_similar_penalizes_kakao_and_excludes_minimal():
    idx, q = _tier_index()
    res = dict(idx.similar_by_vector(q, exclude_ids=set(), limit=10))
    assert "min" not in res, "minimal tier 는 /similar 에서 제외(SIMILAR_MIN_TIER)"
    assert abs(res["rich"] - 1.0) < 1e-4, "rich 무감점"
    assert abs(res["kdesc"] - 0.95) < 1e-4, "kakao_desc 0.95 감점"
    assert res["kdesc"] < res["rich"], "동점 raw 라도 rich 가 위"


def test_similar_old_index_without_candidate_tier_no_penalty():
    idx = VectorIndex(dim=4)
    q = _norm([1, 0, 0, 0])
    idx.add_book("a", reasons=[], desc=q,
                 l1=np.zeros(4, np.float32), l2=np.zeros(4, np.float32))
    # 구 pkl 시뮬: _candidate_tier 속성 자체가 없음 → getattr 폴백 무감점
    if hasattr(idx, "_candidate_tier"):
        del idx._candidate_tier
    idx.build_desc_matrix()
    res = dict(idx.similar_by_vector(q, exclude_ids=set(), limit=10))
    assert abs(res["a"] - 1.0) < 1e-4
