"""batch_enricher 하드닝 테스트.

목적: hard import + 순수 함수 + run() exit code 1 on errors.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import MagicMock, patch


def test_hard_import_no_silent_fallback():
    import batch_enricher
    from lib.retry import with_retry as real_retry
    assert batch_enricher.with_retry is real_retry


def test_assign_font_keyword_match():
    from batch_enricher import assign_font, FONT_POOL
    # SF 키워드 → Do Hyeon
    font = assign_font("SF/판타지", "")
    assert font in FONT_POOL


def test_assign_font_default_when_no_match():
    from batch_enricher import assign_font, DEFAULT_FONT
    font = assign_font("재무제표", "복식부기")
    assert font == DEFAULT_FONT


def test_run_returns_zero_when_no_books():
    import batch_enricher
    with patch.object(batch_enricher, "create_client", return_value=MagicMock()):
        e = batch_enricher.BatchEnricher(dry_run=True)
        with patch.object(e, "fetch_books_needing_enrichment", return_value=[]):
            rc = e.run()
    assert rc == 0


def test_run_returns_one_when_errors():
    import batch_enricher
    with patch.object(batch_enricher, "create_client", return_value=MagicMock()):
        e = batch_enricher.BatchEnricher(dry_run=True)
        books = [{"id": "b1", "cover_url": None, "spine_font": None, "genre": "", "description": ""}]
        with patch.object(e, "fetch_books_needing_enrichment", return_value=books):
            with patch.object(e, "enrich_book", side_effect=RuntimeError("boom")):
                with patch("time.sleep"):
                    rc = e.run()
    assert rc == 1
    assert e.stats["errors"] == 1


def test_run_returns_zero_on_success():
    import batch_enricher
    with patch.object(batch_enricher, "create_client", return_value=MagicMock()):
        e = batch_enricher.BatchEnricher(dry_run=True)
        books = [{"id": "b1", "cover_url": None, "spine_font": None, "genre": "소설", "description": ""}]
        with patch.object(e, "fetch_books_needing_enrichment", return_value=books):
            with patch("time.sleep"):
                rc = e.run()
    assert rc == 0
    assert e.stats["processed"] == 1
