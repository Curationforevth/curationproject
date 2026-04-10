"""C2 (H3): build_index atomic write 패턴 검증.

실제 build() 는 Supabase 의존 — 여기서는 tmp+os.replace 로직만 단위로 검증.
"""
import os
import pickle


def test_tmp_rename_pattern_is_atomic(tmp_path):
    """tmp + os.replace 후 최종 파일만 존재, tmp 는 없어야 한다."""
    out = tmp_path / "index.pkl"
    bundle = {"foo": "bar"}

    tmp = str(out) + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(bundle, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)

    assert out.exists()
    assert not os.path.exists(tmp)
    with open(out, "rb") as f:
        assert pickle.load(f) == bundle


def test_sha256_sidecar_atomic(tmp_path):
    out = tmp_path / "index.pkl.sha256"
    tmp = str(out) + ".tmp"
    with open(tmp, "w") as f:
        f.write("abc123")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    assert out.read_text() == "abc123"
    assert not os.path.exists(tmp)
