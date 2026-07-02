"""ensure_index_present — 부팅 시 인덱스 다운로드 (이미지 밖 인덱스, PR#50).

로컬 HTTP 서버로 릴리즈 자산을 흉내낸다. 검증: 정상 다운로드(원자적 교체),
파일 존재 시 no-op, truncation 검출+재시도 소진 시 fail loud, 재시도 성공.
"""
import http.server
import os
import threading

import pytest

from engine.loader import ensure_index_present

PAYLOAD = b"x" * 1024 * 64


class _Handler(http.server.BaseHTTPRequestHandler):
    truncate = False
    fail_first = 0

    def do_GET(self):
        cls = type(self)
        if cls.fail_first > 0:
            cls.fail_first -= 1
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        body = PAYLOAD[: len(PAYLOAD) // 2] if cls.truncate else PAYLOAD
        self.wfile.write(body)

    def log_message(self, *a):  # 테스트 출력 소음 제거
        pass


@pytest.fixture
def http_url():
    _Handler.truncate = False
    _Handler.fail_first = 0
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/index.pkl"
    srv.shutdown()


def test_downloads_when_missing(tmp_path, http_url):
    pkl = str(tmp_path / "index.pkl")
    assert ensure_index_present(pkl, url=http_url) is True
    assert open(pkl, "rb").read() == PAYLOAD
    assert not os.path.exists(pkl + ".part")  # 원자적 교체 — part 잔재 없음


def test_noop_when_present(tmp_path, http_url):
    pkl = str(tmp_path / "index.pkl")
    open(pkl, "wb").write(b"existing")
    assert ensure_index_present(pkl, url=http_url) is False
    assert open(pkl, "rb").read() == b"existing"  # 덮어쓰지 않음


def test_truncation_fails_loud(tmp_path, http_url):
    _Handler.truncate = True
    pkl = str(tmp_path / "index.pkl")
    with pytest.raises(RuntimeError):
        ensure_index_present(pkl, url=http_url, retries=2, backoff_seconds=0)
    assert not os.path.exists(pkl)          # 손상 파일이 정본 경로에 없음
    assert not os.path.exists(pkl + ".part")


def test_retry_then_success(tmp_path, http_url):
    _Handler.fail_first = 1  # 첫 요청 503 → 재시도로 성공
    pkl = str(tmp_path / "index.pkl")
    assert ensure_index_present(pkl, url=http_url, retries=3, backoff_seconds=0) is True
    assert open(pkl, "rb").read() == PAYLOAD
