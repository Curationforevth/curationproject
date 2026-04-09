import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch
import pytest


def test_compose_embedding_text():
    """title + author + genre + description을 조합"""
    from tier1_embedder import compose_embedding_text

    book = {
        "title": "채식주의자",
        "author": "한강",
        "genre": "소설/시/희곡",
        "description": "한강의 연작소설. 채식을 시작한 여자의 이야기.",
    }
    text = compose_embedding_text(book)
    assert "채식주의자" in text
    assert "한강" in text
    assert "소설" in text
    assert "채식을 시작한" in text


def test_compose_embedding_text_empty_description():
    """description이 없어도 동작"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": "저자", "genre": "소설", "description": ""}
    text = compose_embedding_text(book)
    assert "제목" in text
    assert len(text) > 0


def test_compose_embedding_text_none_fields():
    """None 필드 처리"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": None, "genre": None, "description": None}
    text = compose_embedding_text(book)
    assert "제목" in text


# ============================================================
# Retry / fallback 하드닝 테스트
# ============================================================

class FakeAPIError(Exception):
    def __init__(self, code, message="err"):
        self.code = code
        super().__init__(message)


def _mk_inputs(n):
    return [f"b{i}" for i in range(n)], [[0.1] * 4 for _ in range(n)]


def test_is_statement_timeout_true():
    from tier1_embedder import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("57014")) is True


def test_is_statement_timeout_false_other_pg():
    from tier1_embedder import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("23505")) is False


def test_is_statement_timeout_false_plain():
    from tier1_embedder import _is_statement_timeout
    assert _is_statement_timeout(ValueError("x")) is False


def test_save_embeddings_chunk_dry_run_noop():
    from tier1_embedder import save_embeddings_chunk
    sb = MagicMock()
    book_ids, embs = _mk_inputs(3)
    save_embeddings_chunk(sb, book_ids, embs, dry_run=True)
    sb.table.assert_not_called()


def test_save_embeddings_chunk_raises_on_mismatch():
    from tier1_embedder import save_embeddings_chunk
    sb = MagicMock()
    with pytest.raises(ValueError, match="불일치"):
        save_embeddings_chunk(sb, ["a"], [[0.1], [0.2]], dry_run=True)


def test_fallback_succeeds_first_try():
    """정상 경로 — 50권이 한 번에 저장."""
    import tier1_embedder
    ids, embs = _mk_inputs(50)
    with patch.object(tier1_embedder, "save_embeddings_chunk") as mock_chunk:
        mock_chunk.return_value = None
        saved, failed = tier1_embedder.save_embeddings_with_fallback(
            sb=MagicMock(), book_ids=ids, embeddings=embs, dry_run=False
        )
    assert saved == 50
    assert failed == 0
    assert mock_chunk.call_count == 1


def test_fallback_reduces_to_20_on_timeout():
    """첫 시도 57014 → 20x3 으로 축소해서 전부 성공."""
    import tier1_embedder
    ids, embs = _mk_inputs(50)
    call_count = {"n": 0}

    def chunk_side_effect(sb, bids, embs_, dry_run=False):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise FakeAPIError("57014")
        return None

    with patch.object(tier1_embedder, "save_embeddings_chunk",
                      side_effect=chunk_side_effect):
        saved, failed = tier1_embedder.save_embeddings_with_fallback(
            sb=MagicMock(), book_ids=ids, embeddings=embs, dry_run=False
        )
    assert saved == 50
    assert failed == 0
    # 1 (first 50) + ceil(50/20)=3 = 4 calls
    assert call_count["n"] == 4


def test_fallback_permanent_error_first_try_drops_all():
    """첫 시도 영구 에러 (23505 등) — 재시도 없이 전체 실패."""
    import tier1_embedder
    ids, embs = _mk_inputs(50)
    with patch.object(tier1_embedder, "save_embeddings_chunk",
                      side_effect=FakeAPIError("23505")) as mock_chunk:
        saved, failed = tier1_embedder.save_embeddings_with_fallback(
            sb=MagicMock(), book_ids=ids, embeddings=embs, dry_run=False
        )
    assert saved == 0
    assert failed == 50
    assert mock_chunk.call_count == 1


def test_fallback_persistent_timeout_gives_up_at_single_row():
    """지속적 57014 — 5권이 1권씩 쪼개진 뒤 전부 실패."""
    import tier1_embedder
    ids, embs = _mk_inputs(5)
    with patch.object(tier1_embedder, "save_embeddings_chunk",
                      side_effect=FakeAPIError("57014")):
        saved, failed = tier1_embedder.save_embeddings_with_fallback(
            sb=MagicMock(), book_ids=ids, embeddings=embs, dry_run=False
        )
    assert saved == 0
    assert failed == 5


def test_fallback_mixed_one_bad_row_rest_survive():
    """5권 chunk 중 첫 1권만 영구 실패, 나머지 4권 성공."""
    import tier1_embedder
    ids, embs = _mk_inputs(5)

    def chunk_side_effect(sb, bids, embs_, dry_run=False):
        if len(bids) == 5:
            raise FakeAPIError("57014")
        if len(bids) == 1 and bids[0] == "b0":
            raise FakeAPIError("23505")
        return None

    with patch.object(tier1_embedder, "save_embeddings_chunk",
                      side_effect=chunk_side_effect):
        saved, failed = tier1_embedder.save_embeddings_with_fallback(
            sb=MagicMock(), book_ids=ids, embeddings=embs, dry_run=False
        )
    assert saved == 4
    assert failed == 1


def test_batch_fallbacks_monotone_decreasing():
    from tier1_embedder import BATCH_SIZE_FALLBACKS, BATCH_SIZE
    assert BATCH_SIZE_FALLBACKS == sorted(BATCH_SIZE_FALLBACKS, reverse=True)
    # 첫 시도는 BATCH_SIZE 자체를 쓰므로 fallback 리스트에 포함되면 dead entry.
    assert BATCH_SIZE not in BATCH_SIZE_FALLBACKS
    assert BATCH_SIZE_FALLBACKS[0] < BATCH_SIZE
    assert BATCH_SIZE_FALLBACKS[-1] == 5
