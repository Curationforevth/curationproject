"""engine/user_embed.py — 유저가 추가/좋아요한 책을 스코어링 가능한 벡터로 만든다.

- ensure_books_embedded: book_v3_vectors 에 없는 책을 가용 텍스트로 embed-once 저장 (C1)
- resolve_extra_query_vectors: 정적 인덱스 밖 책의 벡터를 DB 에서 읽어 BookVectors 합성 (C2 헬퍼)
- build_feedback_text / ensure_feedback_embedded: 감정태그+한줄감상 → feedback_embedding (C3)

OpenAI 호출은 ensure_* 안에서만 (백그라운드 recompute 컨텍스트). resolve_* 는 OpenAI 없음.
"""
from __future__ import annotations

import logging

import numpy as np
import requests

from config import (get_supabase, OPENAI_API_KEY, EMBEDDING_MODEL,
                    EMBEDDING_DIMENSIONS)
from engine.index import BookVectors
from engine.utils import to_np

logger = logging.getLogger(__name__)

_MIN_RICH = 200


def _pick_source_text(row: dict) -> tuple[str, bool]:
    """(임베딩 텍스트, provisional). rich≥200 우선 → 카카오 description → title+author+genre.

    provisional=True 는 rich 가 아닌 얕은 텍스트로 임베딩했음을 뜻한다(후속 보강 대상).
    """
    rich = (row.get("rich_description") or "").strip()
    if len(rich) >= _MIN_RICH:
        return rich[:2000], False
    desc = (row.get("description") or "").strip()
    if desc:
        return desc[:2000], True
    parts = [row.get("title") or "", row.get("author") or "", row.get("genre") or ""]
    return " ".join(p for p in parts if p).strip(), True


def _embed_text(text: str) -> list[float]:
    """OpenAI 임베딩 1건. recompute(백그라운드)에서만 호출."""
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBEDDING_MODEL, "input": [text],
              "dimensions": EMBEDDING_DIMENSIONS},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def ensure_books_embedded(book_ids, sb=None, embed_fn=None) -> None:
    """book_v3_vectors 에 없는 책을 가용 텍스트로 embed-once 저장. per-book best-effort.

    이미 있으면 OpenAI 호출 0회(embed-once 축적). 한 책 실패가 나머지를 막지 않는다.
    """
    if not book_ids:
        return
    sb = sb or get_supabase()
    embed_fn = embed_fn or _embed_text
    book_ids = list(dict.fromkeys(book_ids))  # dedup, 순서 유지

    existing = sb.table("book_v3_vectors").select("book_id").in_(
        "book_id", book_ids).execute().data or []
    have = {r["book_id"] for r in existing}
    todo = [b for b in book_ids if b not in have]
    if not todo:
        return

    rows = sb.table("books").select(
        "id,title,author,genre,description,rich_description").in_(
        "id", todo).execute().data or []
    for row in rows:
        try:
            text, provisional = _pick_source_text(row)
            if not text:
                continue
            emb = embed_fn(text)
            sb.table("book_v3_vectors").upsert({
                "book_id": row["id"],
                "desc_embedding": emb,
                "source_text": text[:2000],
                "provisional": provisional,
            }, on_conflict="book_id").execute()
        except Exception as e:  # per-book 격리
            logger.warning("ensure_books_embedded failed b=%s: %s", row.get("id"), e)


def needs_background_embed(ub_rows, bid_order_set) -> bool:
    """캐시미스 시 백그라운드 recompute(임베딩+보강)가 필요한지 — 인메모리 싼 판정.

    좋/싫(rated) 책 중 ① 정적 인덱스 밖이라 임베딩 필요하거나 ② 태그/리뷰 있는데
    feedback_embedding 없으면 True. **rating 가드 필수**: neutral/무평가 행은
    recompute 의 ensure_* 가 처리하지 않으므로(rated=good/bad 만), 가드 없으면 영구히
    True 가 되어 매 요청 recompute 가 도는 버그(코드리뷰 I2). rated 와 동일 조건으로 맞춘다.
    """
    for ub in ub_rows:
        if ub.get("rating") not in ("good", "bad"):
            continue
        if ub.get("book_id") not in bid_order_set:
            return True
        if (ub.get("emotion_tags") or ub.get("review_text")) and not ub.get("feedback_embedding"):
            return True
    return False


def resolve_extra_query_vectors(liked_ids, bid_order_set, sb=None) -> dict:
    """정적 인덱스(bid_order_set) 밖 book_id 만 book_v3_vectors 에서 읽어 BookVectors 합성.

    OpenAI 없음(DB read only). l1/l2 는 zero(W_L1=W_L2=0 이라 무해), reasons=[] (유저 책 reason 없음).
    """
    sb = sb or get_supabase()
    missing = [b for b in liked_ids if b not in bid_order_set]
    if not missing:
        return {}
    rows = sb.table("book_v3_vectors").select(
        "book_id,desc_embedding").in_("book_id", missing).execute().data or []
    dim = EMBEDDING_DIMENSIONS
    out = {}
    for r in rows:
        if not r.get("desc_embedding"):
            continue
        out[r["book_id"]] = BookVectors(
            reasons=[],
            desc=to_np(r["desc_embedding"]),
            l1=np.zeros(dim, np.float32),
            l2=np.zeros(dim, np.float32),
        )
    return out


# --------------------------------------------------------------------------
# C3 — 피드백 신호(감정태그 + 한줄감상)를 라이브 취향에 반영
# --------------------------------------------------------------------------
def build_feedback_text(emotion_tags, review_text):
    """감정태그 + 한줄감상을 임베딩 입력 문자열로 조립. 둘 다 없으면 None.

    원문 무절단(P4 유저 피드백 원문 유지). experiment_confidence.py 의 fmt_feedback
    (이모지/40자 절단)은 디스플레이용이라 차용하지 않는다.
    """
    tags = [t for t in (emotion_tags or []) if t and str(t).strip()]
    review = (review_text or "").strip()
    if not tags and not review:
        return None
    parts = []
    if tags:
        parts.append("태그: " + ", ".join(tags))
    if review:
        parts.append(review)  # 무절단
    return "\n".join(parts)


def ensure_feedback_embedded(rows, sb=None, embed_fn=None) -> None:
    """feedback_embedding 없고 태그/리뷰 있는 user_books 행을 임베딩·갱신. per-book best-effort.

    rows: user_books dict 리스트 (각 행에 user_id, book_id, emotion_tags, review_text,
    feedback_embedding 필요). 성공 시 행의 feedback_embedding 을 in-place 갱신해
    호출측(recompute)이 같은 호출에서 즉시 사용한다.
    """
    sb = sb or get_supabase()
    embed_fn = embed_fn or _embed_text
    for r in rows:
        if r.get("feedback_embedding"):
            continue
        text = build_feedback_text(r.get("emotion_tags"), r.get("review_text"))
        if not text:
            continue
        try:
            emb = embed_fn(text)
            sb.table("user_books").update({"feedback_embedding": emb}).eq(
                "user_id", r["user_id"]).eq("book_id", r["book_id"]).execute()
            r["feedback_embedding"] = emb  # in-place → 호출측 즉시 사용
        except Exception as e:  # per-book 격리
            logger.warning("ensure_feedback_embedded failed b=%s: %s", r.get("book_id"), e)
