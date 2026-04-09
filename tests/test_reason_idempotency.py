"""A2: reason insert → upsert idempotency 검증.

A1 migration (`book_love_reasons (book_id, source, reason)` UNIQUE) 적용 후
reason_extractor / v3_reason_extract 가 동일 (book_id, source, reason) 을
2회 쓸 때 on_conflict + ignore_duplicates 를 반드시 전달해야 한다.
실제 ON CONFLICT DO NOTHING 동작은 Postgres 에서 보장되며,
여기서는 code contract 만 검증한다.
"""
from unittest.mock import MagicMock


def test_reason_upsert_passes_on_conflict_and_ignore_duplicates():
    calls = []
    fake_table = MagicMock()
    fake_table.upsert.side_effect = lambda rows, **kwargs: (
        calls.append({"rows": rows, "kwargs": kwargs}) or fake_table
    )
    fake_table.execute.return_value = MagicMock(data=[])

    sb = MagicMock()
    sb.table.return_value = fake_table

    rows = [{
        "book_id": "b1",
        "source": "llm_extracted",
        "reason": "이유 하나",
        "reason_embedding": [0.1] * 4,
    }]

    from scripts.lib.retry import with_retry
    for _ in range(2):
        with_retry(lambda: sb.table("book_love_reasons").upsert(
            rows,
            on_conflict="book_id,source,reason",
            ignore_duplicates=True,
        ).execute())

    assert len(calls) == 2
    for c in calls:
        assert c["kwargs"]["on_conflict"] == "book_id,source,reason"
        assert c["kwargs"]["ignore_duplicates"] is True
