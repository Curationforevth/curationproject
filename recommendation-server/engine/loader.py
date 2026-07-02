# recommendation-server/engine/loader.py
"""서버 시작 시 로컬 pkl 파일에서 VectorIndex를 로드한다.

빌드: .github/workflows/index-direct.yml 이 생성·커밋하고 GitHub Release
(index-latest)에도 업로드한다. Docker 이미지에는 pkl 을 넣지 않으므로
(.dockerignore — 배포 8.5~12분 → 2~3분대) 부팅 시 파일이 없으면
ensure_index_present() 가 릴리즈에서 내려받는다.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import time
import numpy as np

EXPECTED_VERSIONS = {"v3-float16", "v4-prestacked"}
DEFAULT_PKL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.pkl")

# 공개 repo 의 Release 자산 — LFS(월 1GB 대역폭 쿼터)와 달리 다운로드 제약이
# 사실상 없다. 태그는 고정(index-latest), 자산만 --clobber 로 교체되는 롤링 릴리즈.
INDEX_DOWNLOAD_URL = os.environ.get(
    "INDEX_DOWNLOAD_URL",
    "https://github.com/Curationforevth/curationproject/releases/download/index-latest/index.pkl",
)


def ensure_index_present(pkl_path: str = DEFAULT_PKL_PATH,
                         url: str | None = None,
                         retries: int = 3,
                         backoff_seconds: float = 5.0) -> bool:
    """pkl 이 없으면 릴리즈에서 내려받는다. 반환: 다운로드 수행 여부.

    - 원자성: `.part` 에 스트리밍 후 os.replace — 부분 다운로드가 정본 경로에
      남지 않는다(부팅 중 크래시에도 안전).
    - 무결성: Content-Length 와 실제 바이트 수 비교(truncation 검출).
      unpickle 자체도 손상 시 실패하므로 이중 가드.
    - 실패: retries 회 재시도 후 예외 → 부팅 실패(fail loud). Render 는
      healthCheckPath 미통과 배포를 승격하지 않으므로 이전 배포가 계속 서빙된다.
    """
    if os.path.exists(pkl_path):
        return False
    import requests  # 지연 import — 로컬 테스트/스크립트 경로 부담 없음

    url = url or INDEX_DOWNLOAD_URL
    os.makedirs(os.path.dirname(pkl_path), exist_ok=True)
    part_path = pkl_path + ".part"
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            t0 = time.monotonic()
            with requests.get(url, stream=True, timeout=(10, 120),
                              allow_redirects=True) as res:
                res.raise_for_status()
                expected = int(res.headers.get("Content-Length") or 0)
                written = 0
                with open(part_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8 * 1024 * 1024):
                        f.write(chunk)
                        written += len(chunk)
            if expected and written != expected:
                raise IOError(
                    f"index download truncated: {written}/{expected} bytes")
            os.replace(part_path, pkl_path)
            print(f"[loader] index downloaded: {written / 1e6:.0f}MB "
                  f"in {time.monotonic() - t0:.1f}s from release")
            return True
        except Exception as exc:  # noqa: BLE001 — 재시도 대상 전부
            last_exc = exc
            print(f"[loader] index download attempt {attempt}/{retries} "
                  f"failed: {exc}")
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"index download failed after {retries} attempts") from last_exc


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
