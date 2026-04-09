"""openai_helpers retry/error 단위 테스트 (A8/B6, B7)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch

# conftest autouse 가 OPENAI_API_KEY='fake-openai' 로 세팅한다.


def _fake_response(status_code, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    if status_code >= 400:
        import requests
        err = requests.HTTPError(f"{status_code}")
        err.response = r
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_api_key_raises_when_empty(monkeypatch):
    from scripts.lib.openai_helpers import _get_api_key
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="설정되지 않았습니다"):
        _get_api_key()


def test_call_chat_success_first_try():
    import scripts.lib.openai_helpers as oh
    body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(200, body)):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"ok": True}


def test_call_chat_retries_on_429():
    """429 2회 후 200."""
    import scripts.lib.openai_helpers as oh
    ok_body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    responses = [
        _fake_response(429),
        _fake_response(429),
        _fake_response(200, ok_body),
    ]
    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=responses):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"ok": True}


def test_call_chat_retries_on_500_then_succeeds():
    import scripts.lib.openai_helpers as oh
    ok_body = {"choices": [{"message": {"content": '{"x": 1}'}}]}
    responses = [
        _fake_response(500),
        _fake_response(200, ok_body),
    ]
    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=responses):
        with patch("time.sleep"):
            result = oh.call_chat("hello")
    assert result == {"x": 1}


def test_call_chat_raises_after_max_retries():
    import scripts.lib.openai_helpers as oh
    import requests
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(429)):
        with patch("time.sleep"):
            with pytest.raises(requests.HTTPError):
                oh.call_chat("hello")


def test_call_chat_non_retryable_4xx_fails_fast():
    """400 은 즉시 raise (retry 하지 않음)."""
    import scripts.lib.openai_helpers as oh
    import requests
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        return _fake_response(400)

    with patch("scripts.lib.openai_helpers.requests.post",
               side_effect=fake_post):
        with patch("time.sleep"):
            with pytest.raises(requests.HTTPError):
                oh.call_chat("hello")
    assert call_count["n"] == 1


def test_call_embedding_success():
    import scripts.lib.openai_helpers as oh
    body = {"data": [{"embedding": [0.1, 0.2]}]}
    with patch("scripts.lib.openai_helpers.requests.post",
               return_value=_fake_response(200, body)):
        with patch("time.sleep"):
            result = oh.call_embedding(["text"])
    assert result == [[0.1, 0.2]]
