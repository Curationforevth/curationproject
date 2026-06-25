"""reason build-시점 임베딩 경로(_embed_batch) 단위 테스트 (egress 0, OpenAI mock).

배경: 인덱스 빌드가 Supabase의 NULL reason_embedding 대신 reason 텍스트를 build
시점에 임베딩하도록 바꿈(커밋 f2eb157). 이 경로(_embed_batch 배치/차원/재시도)를
실제로 검증 — 이전 커밋 메시지의 '단위테스트 통과'는 사실이 아니었어 그 정정.
"""
import os
import importlib.util
from unittest.mock import patch, MagicMock

import numpy as np

# config import(OPENAI 등)·load_dotenv 회피용 더미 env
os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")


def _load_builder():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "build_index.py")
    spec = importlib.util.spec_from_file_location("builder_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fake_post(url, headers=None, json=None, timeout=None):
    n = len(json["input"])
    d = json["dimensions"]
    r = MagicMock()
    r.raise_for_status = lambda: None
    r.json = lambda: {"data": [{"embedding": [0.0] * (d - 1) + [1.0]} for _ in range(n)]}
    return r


def test_embed_batch_batches_and_dims():
    bi = _load_builder()
    with patch.object(bi.requests, "post", side_effect=_fake_post):
        out = bi._embed_batch([f"reason {i}" for i in range(300)])  # >2 배치(128)
    assert len(out) == 300
    assert len(out[0]) == bi.EMBEDDING_DIMENSIONS
    # _to_np 가 정규화(스코어링 코사인 전제)
    assert abs(np.linalg.norm(bi._to_np(out[0])) - 1.0) < 1e-5


def test_embed_batch_empty_input():
    bi = _load_builder()
    with patch.object(bi.requests, "post", side_effect=_fake_post):
        assert bi._embed_batch([]) == []


def test_embed_batch_retries_then_raises():
    bi = _load_builder()
    calls = {"n": 0}

    def flaky(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        raise bi.requests.exceptions.ConnectionError("boom")

    with patch.object(bi.requests, "post", side_effect=flaky), \
         patch.object(bi.time, "sleep", lambda *_a, **_k: None):
        raised = False
        try:
            bi._embed_batch(["x"])
        except Exception:
            raised = True
    assert raised, "재시도 소진 후 raise 해야"
    assert calls["n"] == bi.MAX_RETRIES, f"MAX_RETRIES회 시도 기대, 실제 {calls['n']}"
