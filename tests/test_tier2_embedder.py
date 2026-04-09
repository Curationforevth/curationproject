"""tier2_embedder 하드닝 테스트.

목적: hard import + statement_timeout 판별 + run() 이 실패 시 exit code 1.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch


class FakeAPIError(Exception):
    def __init__(self, code, message="err"):
        self.code = code
        super().__init__(message)


def test_hard_import_no_silent_fallback():
    """lib.retry / lib.batch_fallback 가 실제 import 되어야 한다."""
    import tier2_embedder
    # 모듈이 import 한 with_retry 가 fake stub 이 아닌 진짜 retry 인지 확인.
    from lib.retry import with_retry as real_retry
    assert tier2_embedder.with_retry is real_retry
    from lib.batch_fallback import save_with_size_fallback as real_helper
    assert tier2_embedder.save_with_size_fallback is real_helper


def test_is_statement_timeout_true():
    from tier2_embedder import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("57014")) is True


def test_is_statement_timeout_false_other():
    from tier2_embedder import _is_statement_timeout
    assert _is_statement_timeout(FakeAPIError("23505")) is False
    assert _is_statement_timeout(ValueError("x")) is False


def test_batch_size_fallbacks_monotone_decreasing():
    from tier2_embedder import BATCH_SIZE_FALLBACKS, BATCH_SIZE
    assert BATCH_SIZE_FALLBACKS == sorted(BATCH_SIZE_FALLBACKS, reverse=True)
    assert BATCH_SIZE_FALLBACKS[0] < BATCH_SIZE


def test_compose_embedding_minimal():
    """rich_description 의 책소개가 있어야 임베딩 텍스트가 생성된다."""
    from tier2_embedder import compose_embedding
    book = {
        "title": "테스트",
        "author": "저자",
        "rich_description": "[책소개]\n흥미로운 책입니다.\n",
    }
    text, sources = compose_embedding(book)
    assert "테스트" in text
    assert "흥미로운 책" in text
    assert "yes24_intro" in sources


def test_compose_embedding_no_intro_returns_empty():
    from tier2_embedder import compose_embedding
    book = {"title": "x", "rich_description": "[책속으로]\n발췌만 있음"}
    text, sources = compose_embedding(book)
    assert text == ""
    assert sources == []


def test_run_returns_zero_when_no_books():
    """대상 0권이면 exit code 0."""
    import tier2_embedder
    with patch.object(tier2_embedder, "create_client", return_value=MagicMock()):
        embedder = tier2_embedder.Tier2Embedder(dry_run=True)
        with patch.object(embedder, "fetch_books_needing_tier2", return_value=[]):
            rc = embedder.run()
    assert rc == 0


def test_run_returns_one_on_drop_failure():
    """저장 단계에서 모두 실패하면 exit code 1."""
    import tier2_embedder

    book = {
        "id": "b1",
        "title": "T",
        "author": "A",
        "rich_description": "[책소개]\n좋은 책",
    }
    fake_openai = MagicMock()
    fake_openai.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 4)]
    )

    with patch.object(tier2_embedder, "create_client", return_value=MagicMock()):
        embedder = tier2_embedder.Tier2Embedder(dry_run=False)
        embedder._openai_client = fake_openai
        with patch.object(embedder, "fetch_books_needing_tier2", return_value=[book]):
            # save_with_size_fallback 가 모두 실패한 것처럼 mock
            with patch.object(
                tier2_embedder, "save_with_size_fallback", return_value=(0, 1)
            ):
                with patch("time.sleep"):
                    rc = embedder.run()
    assert rc == 1
    assert embedder.stats["drop_failed"] == 1


def test_run_returns_one_on_openai_failure():
    """OpenAI 실패 시 errors 카운트 + exit code 1."""
    import tier2_embedder

    book = {
        "id": "b1",
        "title": "T",
        "author": "A",
        "rich_description": "[책소개]\n좋은 책",
    }
    fake_openai = MagicMock()
    fake_openai.embeddings.create.side_effect = RuntimeError("openai down")

    with patch.object(tier2_embedder, "create_client", return_value=MagicMock()):
        embedder = tier2_embedder.Tier2Embedder(dry_run=False)
        embedder._openai_client = fake_openai
        with patch.object(embedder, "fetch_books_needing_tier2", return_value=[book]):
            with patch("time.sleep"):
                rc = embedder.run()
    assert rc == 1
    assert embedder.stats["errors"] == 1


def test_run_returns_zero_on_full_success():
    """정상 경로: 모든 책 저장 성공 → exit 0."""
    import tier2_embedder

    book = {
        "id": "b1",
        "title": "T",
        "author": "A",
        "rich_description": "[책소개]\n좋은 책",
    }
    fake_openai = MagicMock()
    fake_openai.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 4)]
    )

    with patch.object(tier2_embedder, "create_client", return_value=MagicMock()):
        embedder = tier2_embedder.Tier2Embedder(dry_run=False)
        embedder._openai_client = fake_openai
        with patch.object(embedder, "fetch_books_needing_tier2", return_value=[book]):
            with patch.object(
                tier2_embedder, "save_with_size_fallback", return_value=(1, 0)
            ):
                with patch("time.sleep"):
                    rc = embedder.run()
    assert rc == 0
    assert embedder.stats["embedded"] == 1
    assert embedder.stats["drop_failed"] == 0
