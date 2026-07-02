"""POST /recompute/{user_id} — 좋아요 변경 시 선제 백그라운드 재계산 트리거.

앱이 user_books 를 Supabase 에 직접 쓴 뒤 fire-and-forget 으로 호출한다. 비싼 재계산을
읽기(/recommend) 경로가 아니라 쓰기 시점에 걸어 유저가 추천을 열 땐 캐시가 warm 이도록.
"""
import pytest
from fastapi.testclient import TestClient

from main import app
from auth import verify_jwt
import api.feedback as fb


@pytest.fixture
def client(monkeypatch):
    app.state.index = object()  # not None → 재계산 트리거 게이트 통과
    calls = []
    # 실제 재계산은 돌리지 않고 호출만 기록(백그라운드 태스크는 응답 후 동기 실행됨).
    monkeypatch.setattr(fb, "recompute_recommendations",
                        lambda u, s: calls.append(u))
    app.dependency_overrides[verify_jwt] = lambda: "u1"
    yield TestClient(app), calls
    app.dependency_overrides.clear()


def test_recompute_own_user_triggers_background(client):
    c, calls = client
    r = c.post("/recompute/u1", headers={"Authorization": "Bearer x"})
    assert r.status_code == 202
    assert r.json()["status"] == "recomputing"
    assert calls == ["u1"]  # 백그라운드 재계산이 걸렸다


def test_recompute_other_user_forbidden(client):
    c, calls = client
    r = c.post("/recompute/someone-else", headers={"Authorization": "Bearer x"})
    assert r.status_code == 403
    assert calls == []  # 남의 추천은 재계산 안 함


def test_recompute_no_index_skips_but_ok(client, monkeypatch):
    c, calls = client
    app.state.index = None  # 인덱스 미로드 → 재계산 skip, 그래도 202
    r = c.post("/recompute/u1", headers={"Authorization": "Bearer x"})
    assert r.status_code == 202
    assert calls == []
