# recommendation-server/engine/loader.py
"""서버 시작 시 로컬 pkl 파일에서 VectorIndex를 로드한다.

빌드: scripts/build_index.py로 생성된 data/index.pkl 사용.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import numpy as np

EXPECTED_VERSIONS = {"v3-float16", "v4-prestacked"}
DEFAULT_PKL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")


def _verify_hash(pkl_path: str) -> None:
    hash_path = pkl_path + ".sha256"
    if not os.path.exists(hash_path):
        return  # no hash file = skip (backward compat)
    with open(hash_path) as f:
        expected = f.read().strip()
    sha = hashlib.sha256()
    with open(pkl_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    if sha.hexdigest() != expected:
        raise ValueError(
            f"Index pkl hash mismatch! Expected {expected}, got {sha.hexdigest()}"
        )


from engine.utils import to_np

# backward compat alias
_to_np = to_np


def load_index(pkl_path: str = DEFAULT_PKL_PATH):
    """pkl 번들에서 VectorIndex + books_meta + built_at + v4 프리컴퓨팅 데이터 로드.

    Returns:
        tuple: (
            VectorIndex,
            books_meta dict,
            built_at str,
            prestacked_reasons_f16,   # v4: dict[bid, np.ndarray(N,D,f16)] | None
            desc_matrix_f16,          # v4: np.ndarray(B,D,f16) | None
            agg_reason_matrix_f16,    # v4: np.ndarray(B,D,f16) | None
            bid_order,                # v4: list[str] | None
        )
        v3 번들은 마지막 4개가 None.
    """
    if not os.path.exists(pkl_path):
        raise FileNotFoundError(f"Index pkl not found: {pkl_path}")

    _verify_hash(pkl_path)

    with open(pkl_path, "rb") as f:
        bundle = pickle.load(f)

    version = bundle.get("version", "")
    if version not in EXPECTED_VERSIONS:
        raise ValueError(
            f"Index version mismatch: expected one of {EXPECTED_VERSIONS!r}, got '{version}'"
        )

    index = bundle["index"]
    meta = bundle["meta"]
    built_at = bundle["built_at"]

    if version == "v4-prestacked":
        return (
            index,
            meta,
            built_at,
            bundle["prestacked_reasons_f16"],
            bundle["desc_matrix_f16"],
            bundle["agg_reason_matrix_f16"],
            bundle["bid_order"],
        )
    else:
        # v3-float16 — backward compat
        return index, meta, built_at, None, None, None, None
