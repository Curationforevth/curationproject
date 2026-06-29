"""인덱스 빌드가 source_tier 를 VectorIndex._candidate_tier 로 운반하는지 검증 (Task 9).

importlib 로 빌더를 경로 로드(패키지/파일명 회피). egress·OpenAI 없음.
"""
import os
import importlib.util

import numpy as np

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from engine.index import VectorIndex


def _load_builder():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "build_index.py")
    spec = importlib.util.spec_from_file_location("builder_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _idx():
    idx = VectorIndex(dim=2, dtype=np.float16)
    for bid, v in [("a", [1, 0]), ("b", [0, 1])]:
        idx.add_book(bid, reasons=[], desc=np.array(v, dtype=np.float32),
                     l1=np.zeros(2, np.float32), l2=np.zeros(2, np.float32))
    return idx


def test_set_candidate_tiers_subset_sparse_and_excludes_ghost():
    bi = _load_builder()
    idx = _idx()
    v3 = {"a": {"source_tier": "minimal"},
          "b": {"source_tier": "rich"},
          "ghost": {"source_tier": "kakao_desc"}}  # 인덱스에 add 안 된 책
    bi.set_candidate_tiers(idx, v3)
    assert set(idx._candidate_tier) <= set(idx.book_ids), "키 ⊆ bid_order"
    assert idx._candidate_tier == {"a": "minimal"}, "rich 생략(sparse) + ghost 제외"


def test_set_candidate_tiers_derives_penalty_and_exclude():
    bi = _load_builder()
    idx = _idx()
    bi.set_candidate_tiers(idx, {"a": {"source_tier": "minimal"}})
    idx.build_desc_matrix()
    i_a = idx._desc_bid_to_idx["a"]
    i_b = idx._desc_bid_to_idx["b"]
    assert abs(idx._penalty_vec[i_a] - 0.85) < 1e-6, "minimal → 0.85"
    assert abs(idx._penalty_vec[i_b] - 1.0) < 1e-6, "rich(부재) → 1.0"
    assert "a" in idx._exclude_similar, "minimal → /similar 제외"


def test_set_candidate_tiers_missing_source_tier_defaults_rich():
    bi = _load_builder()
    idx = _idx()
    bi.set_candidate_tiers(idx, {"a": {}, "b": {"source_tier": None}})
    assert idx._candidate_tier == {}, "source_tier 없음/None → rich 취급(sparse 생략)"
