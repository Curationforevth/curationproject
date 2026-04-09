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
