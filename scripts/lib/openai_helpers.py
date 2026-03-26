"""OpenAI API 직접 호출 헬퍼.

openai 패키지 호환 문제(jiter 모듈) 우회를 위해 requests로 직접 호출.
실험 스크립트(experiment_attributes.py)에서 검증된 패턴.
"""

import json
import os

import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072
API_TIMEOUT = 60


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
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=build_chat_payload(prompt, temperature),
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_chat_response(resp.json())


def call_embedding(texts):
    """임베딩 호출 (벡터 리스트 반환)."""
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=build_embedding_payload(texts),
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_embedding_response(resp.json())
