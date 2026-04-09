import os
import sys

# scripts/lib를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _isolate_from_real_services(monkeypatch):
    """A7/B5: 모든 테스트는 기본적으로 실제 네트워크/DB 에서 격리된다.

    - 환경변수를 가짜 값으로 세팅 (service_role 등 실수로 실행되면 401)
    - requests.{post,get,put,delete} 를 명시적으로 패치하지 않은 테스트에서
      호출되면 즉시 실패
    - supabase.create_client 을 MagicMock 으로 대체

    실제 네트워크 테스트가 필요하면 각 테스트가 개별 monkeypatch 로 복구.
    """
    fake_env = {
        "SUPABASE_URL": "http://test.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role",
        "SUPABASE_ANON_KEY": "fake-anon",
        "OPENAI_API_KEY": "fake-openai",
        "ALADIN_TTB_KEY": "fake-aladin",
        "KAKAO_REST_API_KEY": "fake-kakao",
        "DATA4LIBRARY_API_KEY": "fake-data4library",
        "RECOMMENDATION_SERVER_URL": "http://test.invalid",
    }
    for k, v in fake_env.items():
        monkeypatch.setenv(k, v)

    import requests

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Test attempted real HTTP call. Use monkeypatch to stub it."
        )

    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "put", _blocked)
    monkeypatch.setattr(requests, "delete", _blocked)

    try:
        import supabase
        from unittest.mock import MagicMock
        monkeypatch.setattr(supabase, "create_client",
                            lambda *a, **k: MagicMock())
    except ImportError:
        pass

    yield
