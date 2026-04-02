import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestVectorIndex:
    def test_add_and_get_book_vectors(self):
        idx = VectorIndex(dim=4)
        reasons = [_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])]
        idx.add_book("b1", reasons=reasons, desc=_norm([1, 1, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        bv = idx.get_book("b1")
        assert bv is not None
        assert len(bv.reasons) == 2
        assert bv.desc.shape == (4,)

    def test_get_missing_book_returns_none(self):
        idx = VectorIndex(dim=4)
        assert idx.get_book("missing") is None

    def test_cosine_sim_identical(self):
        idx = VectorIndex(dim=4)
        v = _norm([1, 0, 0, 0])
        sim = idx.cosine_sim(v, v)
        assert abs(sim - 1.0) < 1e-5

    def test_cosine_sim_orthogonal(self):
        idx = VectorIndex(dim=4)
        a = _norm([1, 0, 0, 0])
        b = _norm([0, 1, 0, 0])
        sim = idx.cosine_sim(a, b)
        assert abs(sim) < 1e-5

    def test_desc_matrix_similar(self):
        idx = VectorIndex(dim=4)
        idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b2", reasons=[], desc=_norm([0.9, 0.1, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b3", reasons=[], desc=_norm([0, 0, 1, 0]),
                     l1=_norm([0, 0, 1, 0]), l2=_norm([0, 0, 0, 1]))
        idx.build_desc_matrix()
        sims = idx.similar_by_desc("b1", limit=2)
        assert sims[0][0] == "b2"

    def test_book_ids_list(self):
        idx = VectorIndex(dim=4)
        idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b2", reasons=[], desc=_norm([0, 1, 0, 0]),
                     l1=_norm([0, 1, 0, 0]), l2=_norm([1, 0, 0, 0]))
        assert set(idx.book_ids) == {"b1", "b2"}
