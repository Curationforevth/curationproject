from __future__ import annotations

import numpy as np


def to_np(vec) -> np.ndarray:
    """DB 벡터(리스트 또는 문자열)를 L2-정규화된 numpy float32로 변환."""
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a
