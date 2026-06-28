from __future__ import annotations

import re

import numpy as np


def clean_html(text) -> str:
    """HTML 태그 제거. None이면 빈 문자열.

    scripts/lib/genre_parser.clean_html 과 동작 동일(배포 경계로 코드 미공유 — 동등성
    테스트로 동기화). 평문에 idempotent. 배치/라이브 임베딩 폴백 게이트를 일치시킨다.
    """
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


def to_np(vec) -> np.ndarray:
    """DB 벡터(리스트 또는 문자열)를 L2-정규화된 numpy float32로 변환."""
    if isinstance(vec, str):
        vec = [float(x) for x in vec.strip("[]").split(",")]
    a = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(a)
    return a / norm if norm > 0 else a
