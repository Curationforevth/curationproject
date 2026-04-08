"""POST /similar/union — selected books의 평균 벡터로 top-K."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import app
from auth import verify_jwt
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


@pytest.fixture
def client_with_index():
    idx = VectorIndex(dim=8)
    novel_l1 = _norm([1, 0, 0, 0, 0, 0, 0, 0])
    econ_l1 = _norm([0, 1, 0, 0, 0, 0, 0, 0])
    idx.add_book("b1", desc=_norm([1, 0, 0, 0, 0.5, 0, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b3", desc=_norm([1, 0, 0, 0, 0, 0.9, 0, 0]),
                 l1=novel_l1, l2=novel_l1, reasons=[])
    idx.add_book("b4", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=econ_l1, l2=econ_l1, reasons=[])
    idx.add_book("b5", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=econ_l1, l2=econ_l1, reasons=[])
    idx.build_desc_matrix()

    app.state.index = idx
    app.state.books_meta = {
        "b1": {"title": "B1", "author": "A1", "cover_url": "u1"},
        "b2": {"title": "B2", "author": "A2", "cover_url": "u2"},
        "b3": {"title": "B3", "author": "A3", "cover_url": "u3"},
        "b4": {"title": "B4", "author": "A4", "cover_url": "u4"},
        "b5": {"title": "B5", "author": "A5", "cover_url": "u5"},
    }
    app.state.built_at = "test"
    app.dependency_overrides[verify_jwt] = lambda: "test-user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_similar_union_returns_top_k_excluding_input(client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["b1", "b2"], "limit": 3},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "similar" in body
    ids = [s["book_id"] for s in body["similar"]]
    assert "b1" not in ids
    assert "b2" not in ids
    assert len(body["similar"]) == 3


def test_similar_union_with_unknown_book_ids_skips_them(client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["b1", "doesnotexist"], "limit": 2},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    body = r.json()
    ids = [s["book_id"] for s in body["similar"]]
    assert "b1" not in ids
    assert "doesnotexist" not in ids
    assert len(body["similar"]) == 2


def test_similar_union_empty_input_returns_empty(client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": [], "limit": 5},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    assert r.json()["similar"] == []


def test_similar_union_all_unknown_returns_empty(client_with_index):
    r = client_with_index.post(
        "/similar/union",
        json={"book_ids": ["nope1", "nope2"], "limit": 5},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert r.status_code == 200
    assert r.json()["similar"] == []
