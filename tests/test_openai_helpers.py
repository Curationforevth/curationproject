"""OpenAI 헬퍼 유닛 테스트 — API 호출 없이 순수 함수만"""
import json
import pytest
from lib.openai_helpers import (
    build_chat_payload,
    build_embedding_payload,
    parse_chat_response,
    parse_embedding_response,
)


def test_build_chat_payload():
    payload = build_chat_payload("테스트 프롬프트", temperature=0.3)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][0]["content"] == "테스트 프롬프트"
    assert payload["temperature"] == 0.3
    assert payload["response_format"]["type"] == "json_object"


def test_build_chat_payload_default_temperature():
    payload = build_chat_payload("프롬프트")
    assert payload["temperature"] == 0.3


def test_build_embedding_payload_single():
    payload = build_embedding_payload(["hello"])
    assert payload["model"] == "text-embedding-3-large"
    assert payload["input"] == ["hello"]


def test_build_embedding_payload_batch():
    texts = [f"text_{i}" for i in range(5)]
    payload = build_embedding_payload(texts)
    assert len(payload["input"]) == 5
    assert payload["input"] == texts


def test_parse_chat_response():
    mock_response = {
        "choices": [{"message": {"content": '{"reasons": ["이유1", "이유2"]}'}}]
    }
    result = parse_chat_response(mock_response)
    assert result == {"reasons": ["이유1", "이유2"]}


def test_parse_chat_response_complex_json():
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": '{"attributes": {"key1": 0.5, "key2": 0.8}, "score": 42}'
                }
            }
        ]
    }
    result = parse_chat_response(mock_response)
    assert result["attributes"]["key1"] == 0.5
    assert result["score"] == 42


def test_parse_embedding_response():
    mock_response = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
        ]
    }
    result = parse_embedding_response(mock_response)
    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
    assert result[1] == [0.4, 0.5, 0.6]


def test_parse_embedding_response_single():
    mock_response = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    result = parse_embedding_response(mock_response)
    assert len(result) == 1
    assert result[0] == [0.1, 0.2, 0.3]
