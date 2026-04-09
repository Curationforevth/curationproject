"""conftest autouse fixture 가 실제 네트워크/환경을 차단하는지 확인 (A7/B5)."""
import os
import pytest
import requests


def test_fake_env_vars_set():
    assert os.environ["SUPABASE_URL"] == "http://test.invalid"
    assert os.environ["OPENAI_API_KEY"] == "fake-openai"


def test_requests_post_blocked():
    with pytest.raises(RuntimeError, match="real HTTP call"):
        requests.post("https://api.openai.com/v1/chat/completions")


def test_requests_get_blocked():
    with pytest.raises(RuntimeError, match="real HTTP call"):
        requests.get("https://api.example.com/")


def test_supabase_create_client_returns_mock():
    import supabase
    client = supabase.create_client("x", "y")
    # MagicMock — 아무 method 호출해도 에러 없음
    client.table("books").select("*").execute()
