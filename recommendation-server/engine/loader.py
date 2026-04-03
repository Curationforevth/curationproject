# recommendation-server/engine/loader.py
"""서버 시작 시 로컬 pkl 파일에서 VectorIndex를 로드한다.

빌드: scripts/build_index.py로 생성된 data/index.pkl 사용.
"""
import os
import pickle
import numpy as np

EXPECTED_VERSION = "v3-float16"
DEFAULT_PKL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")


def _to_np(vec) -> np.ndarray:
    """DB 벡터(리스트 또는 문자열)를 L2-정규화된 numpy float32로 변환."""
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a


def load_index(pkl_path: str = DEFAULT_PKL_PATH):
    """pkl 번들에서 VectorIndex + books_meta + built_at 로드.

    Returns:
        tuple: (VectorIndex, books_meta dict, built_at str)
    """
    if not os.path.exists(pkl_path):
        raise FileNotFoundError(f"Index pkl not found: {pkl_path}")

    with open(pkl_path, "rb") as f:
        bundle = pickle.load(f)

    version = bundle.get("version", "")
    if version != EXPECTED_VERSION:
        raise ValueError(
            f"Index version mismatch: expected '{EXPECTED_VERSION}', got '{version}'"
        )

    return bundle["index"], bundle["meta"], bundle["built_at"]
