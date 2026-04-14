import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pickle
import tempfile
import numpy as np
import pytest
from engine.index import VectorIndex
from engine.loader import load_index, _to_np

DIM = 4


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def _make_v3_bundle(tmp_path):
    idx = VectorIndex(dim=DIM, dtype=np.float16)
    idx.add_book("b1", reasons=[_norm([1, 0, 0, 0])],
                 desc=_norm([1, 0, 0, 0]), l1=_norm([1, 0, 0, 0]),
                 l2=_norm([0, 1, 0, 0]))
    idx.build_desc_matrix()
    meta = {"b1": {"title": "Test", "author": "A", "cover_url": None}}
    bundle = {
        "index": idx,
        "meta": meta,
        "built_at": "2026-04-03T12:00:00",
        "version": "v3-float16",
    }
    pkl_path = tmp_path / "index.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(bundle, f)
    return pkl_path, idx, meta


def _make_v4_bundle(tmp_path):
    idx = VectorIndex(dim=DIM, dtype=np.float16)
    idx.add_book("b1", reasons=[_norm([1, 0, 0, 0]), _norm([0, 1, 0, 0])],
                 desc=_norm([1, 0, 0, 0]), l1=_norm([1, 0, 0, 0]),
                 l2=_norm([0, 1, 0, 0]))
    idx.add_book("b2", reasons=[],
                 desc=_norm([0, 0, 1, 0]), l1=_norm([0, 0, 1, 0]),
                 l2=_norm([0, 0, 0, 1]))
    idx.build_desc_matrix()
    meta = {
        "b1": {"title": "Test1", "author": "A", "cover_url": None},
        "b2": {"title": "Test2", "author": "B", "cover_url": None},
    }

    bid_order = list(idx._books.keys())
    prestacked_f16 = {}
    for bid in bid_order:
        bv = idx.get_book(bid)
        if bv.reasons:
            prestacked_f16[bid] = np.stack(bv.reasons).astype(np.float16)
        else:
            prestacked_f16[bid] = np.empty((0, DIM), dtype=np.float16)

    desc_matrix_f16 = np.stack([idx.get_book(bid).desc for bid in bid_order]).astype(np.float16)

    agg_list = []
    for bid in bid_order:
        bv = idx.get_book(bid)
        if bv.reasons:
            mean_vec = np.mean(np.stack(bv.reasons).astype(np.float32), axis=0)
            norm = np.linalg.norm(mean_vec)
            agg_list.append(
                (mean_vec / norm).astype(np.float16) if norm > 0 else mean_vec.astype(np.float16))
        else:
            agg_list.append(np.zeros(DIM, dtype=np.float16))
    agg_reason_matrix_f16 = np.stack(agg_list)

    bundle = {
        "index": idx,
        "meta": meta,
        "built_at": "2026-04-10T09:00:00",
        "version": "v4-prestacked",
        "prestacked_reasons_f16": prestacked_f16,
        "desc_matrix_f16": desc_matrix_f16,
        "agg_reason_matrix_f16": agg_reason_matrix_f16,
        "bid_order": bid_order,
    }
    pkl_path = tmp_path / "index_v4.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(bundle, f)
    return pkl_path, bid_order, prestacked_f16, desc_matrix_f16, agg_reason_matrix_f16


class TestPklLoader:
    def test_load_index_from_pkl(self, tmp_path):
        """pkl 번들에서 index + meta를 정상 로드 (v3 backward compat)."""
        pkl_path, idx, meta = _make_v3_bundle(tmp_path)

        loaded_idx, loaded_meta, loaded_built_at, *_ = load_index(str(pkl_path))
        assert len(loaded_idx.book_ids) == 1
        assert loaded_meta["b1"]["title"] == "Test"
        assert loaded_built_at == "2026-04-03T12:00:00"

    def test_load_index_missing_file(self):
        """존재하지 않는 pkl 파일이면 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_index("/nonexistent/path.pkl")

    def test_load_index_invalid_version(self, tmp_path):
        """version이 다르면 ValueError."""
        bundle = {"index": None, "meta": {}, "built_at": "", "version": "v2-old"}
        pkl_path = tmp_path / "index.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump(bundle, f)
        with pytest.raises(ValueError, match="version"):
            load_index(str(pkl_path))

    def test_load_v3_backward_compat(self, tmp_path):
        """v3 번들은 7-tuple 반환, 마지막 4개는 None."""
        pkl_path, _, _ = _make_v3_bundle(tmp_path)
        result = load_index(str(pkl_path))
        assert len(result) == 7
        loaded_idx, loaded_meta, loaded_built_at, prestacked, desc_mat, agg_mat, bid_order = result
        assert prestacked is None
        assert desc_mat is None
        assert agg_mat is None
        assert bid_order is None

    def test_load_v4_prestacked(self, tmp_path):
        """v4 번들은 7-tuple 반환, 행렬/딕셔너리 정상 로드."""
        pkl_path, bid_order_orig, prestacked_orig, desc_mat_orig, agg_mat_orig = \
            _make_v4_bundle(tmp_path)
        result = load_index(str(pkl_path))
        assert len(result) == 7
        loaded_idx, loaded_meta, loaded_built_at, prestacked, desc_mat, agg_mat, bid_order = result

        # 기본 필드
        assert len(loaded_idx.book_ids) == 2
        assert loaded_built_at == "2026-04-10T09:00:00"

        # bid_order
        assert bid_order == bid_order_orig

        # prestacked_reasons: b1은 (2, DIM), b2는 (0, DIM)
        assert prestacked["b1"].shape == (2, DIM)
        assert prestacked["b2"].shape == (0, DIM)
        assert prestacked["b1"].dtype == np.float16

        # desc_matrix shape
        assert desc_mat.shape == (2, DIM)
        assert desc_mat.dtype == np.float16

        # agg_reason_matrix shape
        assert agg_mat.shape == (2, DIM)
        assert agg_mat.dtype == np.float16

        # b2는 reasons 없으므로 agg zero vector
        b2_idx = bid_order.index("b2")
        assert np.allclose(agg_mat[b2_idx].astype(np.float32), 0.0)


class TestToNp:
    def test_string_vector(self):
        result = _to_np("[1.0, 0.0, 0.0]")
        assert result.shape == (3,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_list_vector(self):
        result = _to_np([3.0, 4.0])
        assert result.shape == (2,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_zero_vector(self):
        result = _to_np([0.0, 0.0, 0.0])
        assert np.allclose(result, 0.0)
