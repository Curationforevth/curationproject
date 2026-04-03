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

    def test_float16_storage_and_similarity(self):
        """float16으로 저장해도 cosine sim 결과가 float32와 거의 동일."""
        idx32 = VectorIndex(dim=4)
        idx16 = VectorIndex(dim=4, dtype=np.float16)
        desc = _norm([1, 0.5, 0.2, 0])
        l1 = _norm([1, 0, 0, 0])
        l2 = _norm([0, 1, 0, 0])
        reasons = [_norm([1, 0.3, 0, 0]), _norm([0, 1, 0.5, 0])]

        idx32.add_book("b1", reasons=reasons, desc=desc, l1=l1, l2=l2)
        idx16.add_book("b1", reasons=reasons, desc=desc, l1=l1, l2=l2)

        bv32 = idx32.get_book("b1")
        bv16 = idx16.get_book("b1")
        assert bv32.desc.dtype == np.float32
        assert bv16.desc.dtype == np.float16
        # 유사도 차이 < 0.01
        sim32 = float(np.dot(bv32.desc.astype(np.float32), bv32.l1.astype(np.float32)))
        sim16 = float(np.dot(bv16.desc.astype(np.float32), bv16.l1.astype(np.float32)))
        assert abs(sim32 - sim16) < 0.01

    def test_float16_similar_by_desc(self):
        """float16 인덱스에서 similar_by_desc가 동일한 순위를 반환."""
        idx = VectorIndex(dim=4, dtype=np.float16)
        idx.add_book("b1", reasons=[], desc=_norm([1, 0, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b2", reasons=[], desc=_norm([0.9, 0.1, 0, 0]),
                     l1=_norm([1, 0, 0, 0]), l2=_norm([0, 1, 0, 0]))
        idx.add_book("b3", reasons=[], desc=_norm([0, 0, 1, 0]),
                     l1=_norm([0, 0, 1, 0]), l2=_norm([0, 0, 0, 1]))
        idx.build_desc_matrix()
        sims = idx.similar_by_desc("b1", limit=2)
        assert sims[0][0] == "b2"
