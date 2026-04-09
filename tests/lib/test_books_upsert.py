"""B6: books_upsert richer-merge helper 단위 테스트."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import MagicMock

from scripts.lib.books_upsert import merge_richer, upsert_books_rich_merge


def test_merge_richer_longer_string_wins():
    existing = {"title": "짧은 제목", "author": "A"}
    new = {"title": "훨씬 더 긴 풀 제목", "author": "A"}
    merged = merge_richer(existing, new)
    assert merged["title"] == "훨씬 더 긴 풀 제목"


def test_merge_richer_shorter_new_is_rejected():
    existing = {"title": "매우 길고 상세한 원본 제목"}
    new = {"title": "짧은것"}
    merged = merge_richer(existing, new)
    assert merged["title"] == "매우 길고 상세한 원본 제목"


def test_merge_richer_empty_old_takes_new():
    existing = {"title": "", "cover_url": None}
    new = {"title": "제목", "cover_url": "http://x/y.jpg"}
    merged = merge_richer(existing, new)
    assert merged["title"] == "제목"
    assert merged["cover_url"] == "http://x/y.jpg"


def test_merge_richer_empty_new_keeps_old():
    existing = {"title": "원본 제목"}
    new = {"title": ""}
    merged = merge_richer(existing, new)
    assert merged["title"] == "원본 제목"


def test_merge_richer_numeric_max_wins():
    existing = {"loan_count": 10, "sales_point": 100}
    new = {"loan_count": 5, "sales_point": 500}
    merged = merge_richer(existing, new)
    assert merged["loan_count"] == 10  # 기존 유지
    assert merged["sales_point"] == 500  # 새 값 채택


def test_merge_richer_source_updated_to_latest():
    existing = {"source": "aladin"}
    new = {"source": "data4library"}
    merged = merge_richer(existing, new)
    assert merged["source"] == "data4library"


def test_upsert_books_rich_merge_inserts_new_row():
    """기존 row 없음 → new 그대로 upsert."""
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value \
        .execute.return_value.data = []

    new = [{"isbn": "123", "title": "새책", "author": "X", "source": "aladin"}]
    n = upsert_books_rich_merge(sb, new)
    assert n == 1

    # upsert 호출 확인
    upsert_calls = sb.table.return_value.upsert.call_args_list
    assert len(upsert_calls) == 1
    sent_rows = upsert_calls[0].args[0]
    assert sent_rows[0]["isbn"] == "123"


def test_upsert_books_rich_merge_merges_with_existing():
    """기존 row 있음 → 더 긴 title 유지, 언급 안 된 필드도 보존."""
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value \
        .execute.return_value.data = [
        {"isbn": "123", "title": "매우 길고 상세한 원본 제목",
         "author": "기존저자", "publisher": "X",
         "cover_url": "http://old", "loan_count": 100,
         "sales_point": 100, "source": "data4library",
         # new 에 없는 필드들 — upsert 후에도 살아남아야 함
         "description": "기존 description",
         "rich_description": "기존 rich",
         "genre": "소설"},
    ]

    new = [{"isbn": "123", "title": "짧은제목", "author": "",
            "source": "aladin", "loan_count": 50, "sales_point": 200}]
    upsert_books_rich_merge(sb, new)

    upsert_calls = sb.table.return_value.upsert.call_args_list
    merged_row = upsert_calls[0].args[0][0]
    # 더 긴 title 이 유지됨
    assert merged_row["title"] == "매우 길고 상세한 원본 제목"
    # 빈 새 author 가 기존 author 를 덮어쓰지 않음
    assert merged_row["author"] == "기존저자"
    # new 에 없는 publisher 도 그대로 보존
    assert merged_row["publisher"] == "X"
    # 숫자는 큰 쪽
    assert merged_row["loan_count"] == 100
    assert merged_row["sales_point"] == 200
    # source 는 최신
    assert merged_row["source"] == "aladin"
    # new 에 전혀 언급되지 않은 필드들 (description, rich_description, genre)
    # 이 merged 에 살아남아야 cross-pipeline overwrite 방지.
    assert merged_row["description"] == "기존 description"
    assert merged_row["rich_description"] == "기존 rich"
    assert merged_row["genre"] == "소설"
