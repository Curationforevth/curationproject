import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def small_index():
    """5권짜리 테스트 인덱스. 소설2 + 경제2 + 과학1."""
    idx = VectorIndex(dim=8)
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    sci_l1 = _norm([0, 0, 1, 0, 0, 0, 0, 0])
    novel_l2a = _norm([1, 0, 0, 0, 0.3, 0, 0, 0])
    novel_l2b = _norm([1, 0, 0, 0, 0, 0.3, 0, 0])
    econ_l2 = _norm([0, 1, 0, 0, 0, 0, 0.3, 0])
    sci_l2 = _norm([0, 0, 1, 0, 0, 0, 0, 0.3])

    idx.add_book("novel1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                 l1=novel_l1, l2=novel_l2a,
                 reasons=[_norm([1, 0, 0, 0, 0.8, 0, 0, 0]),
                          _norm([1, 0, 0, 0, 0.3, 0.5, 0, 0])])
    idx.add_book("novel2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l2b,
                 reasons=[_norm([1, 0, 0, 0, 0, 0.9, 0, 0])])
    idx.add_book("econ1", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l2,
                 reasons=[_norm([0, 1, 0, 0, 0, 0, 0.8, 0]),
                          _norm([0, 1, 0, 0, 0, 0, 0.3, 0.5])])
    idx.add_book("econ2", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l2,
                 reasons=[_norm([0, 1, 0, 0, 0, 0, 0.9, 0])])
    idx.add_book("sci1", desc=_norm([0, 0, 1, 0, 0, 0, 0, 0.5]),
                 l1=sci_l1, l2=sci_l2,
                 reasons=[_norm([0, 0, 1, 0, 0, 0, 0, 0.8])])
    idx.build_desc_matrix()
    return idx
