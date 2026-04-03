import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pickle
import tempfile
import numpy as np
import pytest
from engine.index import VectorIndex
from engine.loader import load_index, _to_np


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


class TestPklLoader:
    def test_load_index_from_pkl(self, tmp_path):
        """pkl 번들에서 index + meta를 정상 로드."""
        idx = VectorIndex(dim=4, dtype=np.float16)
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

        loaded_idx, loaded_meta, loaded_built_at = load_index(str(pkl_path))
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
