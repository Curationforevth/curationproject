"""회귀: desc-dedup 로드 경로(per-book BookVectors.desc=None + 번들 matrix attach)에서
/similar 가 동작해야 한다.

prod 인덱스는 index_rebuild_direct.py 가 메모리 절감을 위해 per-book `bv.desc=None` 으로
strip 하고 desc 를 번들 matrix 1벌로만 보유한다(로드 시 attach_desc_matrix). 이 조건에서
similar_by_desc / similar_union 이 `bv.desc`(=None)를 직접 읽으면 500 이 난다 —
strip 이후엔 반드시 index.desc_of(book_id)(matrix 조회)를 써야 한다.

이전 테스트(test_similar_by_vector / test_similar_union)는 fixture 가 bv.desc 를 채운 채
둬서 이 회귀를 못 잡았다. 여기선 prod 로드 경로를 그대로 재현한다.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from main import app
from auth import verify_jwt
from engine.index import VectorIndex


def _norm(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def _stripped_index():
    """prod desc-dedup 로드 경로 재현: per-book desc strip + 번들 matrix attach."""
    idx = VectorIndex(dim=8)
    idx.add_book("novel1", desc=_norm([1, 0, 0, 0, 0.5, 0.2, 0, 0]),
                 l1=np.zeros(8, np.float32), l2=np.zeros(8, np.float32), reasons=[])
    idx.add_book("novel2", desc=_norm([1, 0, 0, 0, 0.2, 0.8, 0, 0]),
                 l1=np.zeros(8, np.float32), l2=np.zeros(8, np.float32), reasons=[])
    idx.add_book("econ1", desc=_norm([0, 1, 0, 0, 0, 0, 0.5, 0]),
                 l1=np.zeros(8, np.float32), l2=np.zeros(8, np.float32), reasons=[])
    idx.add_book("econ2", desc=_norm([0, 1, 0, 0, 0, 0, 0.8, 0.2]),
                 l1=np.zeros(8, np.float32), l2=np.zeros(8, np.float32), reasons=[])
    # 빌드측: 번들 matrix 생성. 로드측: per-book strip 후 attach (index_rebuild_direct.py:120).
    bid_order = list(idx._books.keys())
    matrix = np.stack([idx._books[b].desc for b in bid_order])
    for b in bid_order:
        idx._books[b].desc = None
    idx.attach_desc_matrix(matrix, bid_order)
    return idx


def test_similar_by_desc_works_when_desc_stripped():
    """엔진: strip 된 인덱스에서도 similar_by_desc 가 matrix(desc_of)로 결과를 줘야 한다."""
    idx = _stripped_index()
    res = idx.similar_by_desc("novel1", limit=3)
    assert res, "strip 후에도 similar_by_desc 결과가 있어야 함 (bv.desc=None → desc_of matrix 조회)"
    ids = [r[0] for r in res]
    assert "novel1" not in ids, "seed 는 결과에서 제외"
    assert "novel2" in ids, "최근접 이웃(novel2)이 상위에 와야 함"


@pytest.fixture
def stripped_client():
    idx = _stripped_index()
    app.state.index = idx
    app.state.books_meta = {
        b: {"title": b.upper(), "author": f"A_{b}", "cover_url": f"u_{b}"}
        for b in ("novel1", "novel2", "econ1", "econ2")
    }
    app.state.built_at = "test"
    app.dependency_overrides[verify_jwt] = lambda: "test-user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_similar_route_with_stripped_desc(stripped_client):
    """GET /similar/{id}: strip 인덱스에서 500 이 아니라 200 + 비어있지 않은 결과."""
    r = stripped_client.get("/similar/novel1", params={"limit": 3},
                            headers={"Authorization": "Bearer faketoken"})
    assert r.status_code == 200, f"strip 인덱스에서 /similar 500 회귀: {r.text[:200]}"
    sim = r.json()["similar"]
    assert sim, "similar 결과가 비어있으면 안 됨"
    assert "novel1" not in [s["book_id"] for s in sim]


def test_similar_union_route_with_stripped_desc(stripped_client):
    """POST /similar/union: strip 인덱스에서 500 이 아니라 200 + 결과."""
    r = stripped_client.post("/similar/union",
                             json={"book_ids": ["novel1", "novel2"], "limit": 2},
                             headers={"Authorization": "Bearer faketoken"})
    assert r.status_code == 200, f"strip 인덱스에서 /similar/union 500 회귀: {r.text[:200]}"
    body = r.json()
    ids = [s["book_id"] for s in body["similar"]]
    assert "novel1" not in ids and "novel2" not in ids
    assert len(body["similar"]) >= 1
