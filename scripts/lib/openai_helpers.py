"""OpenAI API 직접 호출 헬퍼.

openai 패키지 호환 문제(jiter 모듈) 우회를 위해 requests 로 직접 호출.
429/5xx 에는 지수 백오프 재시도 (A8/B6, B7).
"""

import json
import os
import time

import requests


def _get_api_key() -> str:
    """OPENAI_API_KEY 환경변수 조회 + 검증.

    빈 값일 때 raise 해서 런타임 401 이 아닌 설정 오류로 즉시 실패.
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일 확인."
        )
    return key


CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 2000  # pgvector 인덱스 최대 2000차원, Matryoshka 로 축소
API_TIMEOUT = 60

MAX_RETRIES = 4
BACKOFF_BASE = 1.0  # 1s, 2s, 4s, 8s


def _is_retryable(status: int) -> bool:
    """429 (rate limit), 500/502/503/504 (transient server) 만 재시도."""
    return status == 429 or 500 <= status < 600


def _call_with_retry(url: str, payload: dict) -> dict:
    """requests.post + 재시도. 4xx (429 제외) 은 즉시 raise."""
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=API_TIMEOUT)
        except requests.RequestException as e:
            # 네트워크/연결 에러 — 재시도 대상
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                raise
            delay = BACKOFF_BASE * (2 ** attempt)
            print(f"  ⚠ OpenAI network err retry {attempt+1}/{MAX_RETRIES}: {e}")
            time.sleep(delay)
            continue

        if resp.status_code < 400:
            return resp.json()
        if not _is_retryable(resp.status_code):
            # 영구 HTTP 에러 — 즉시 raise (재시도 금지)
            resp.raise_for_status()
        # retryable HTTP — 마지막 시도면 raise, 아니면 backoff
        if attempt == MAX_RETRIES - 1:
            resp.raise_for_status()
        delay = BACKOFF_BASE * (2 ** attempt)
        print(f"  ⚠ OpenAI {resp.status_code} retry {attempt+1}/{MAX_RETRIES} (sleep {delay}s)")
        time.sleep(delay)
    raise RuntimeError(f"OpenAI call failed after {MAX_RETRIES} retries: {last_exc}")


def build_chat_payload(prompt, temperature=0.3):
    """LLM 채팅 요청 페이로드 구성."""
    return {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }


def build_embedding_payload(texts):
    """임베딩 요청 페이로드 구성."""
    return {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "dimensions": EMBEDDING_DIMENSIONS,
    }


def parse_chat_response(response_json):
    """LLM 응답에서 JSON 파싱."""
    content = response_json["choices"][0]["message"]["content"]
    return json.loads(content)


def parse_embedding_response(response_json):
    """임베딩 응답에서 벡터 리스트 추출."""
    return [d["embedding"] for d in response_json["data"]]


def call_chat(prompt, temperature=0.3):
    """LLM 호출 (JSON 응답 반환)."""
    data = _call_with_retry(
        "https://api.openai.com/v1/chat/completions",
        build_chat_payload(prompt, temperature),
    )
    return parse_chat_response(data)


def call_embedding(texts):
    """임베딩 호출 (벡터 리스트 반환)."""
    data = _call_with_retry(
        "https://api.openai.com/v1/embeddings",
        build_embedding_payload(texts),
    )
    return parse_embedding_response(data)
