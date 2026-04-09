"""YES24 스크래퍼 순수 함수 테스트"""
import pytest


def test_isbn_matches_exact():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "9788954681179") is True


def test_isbn_matches_partial():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "8954681179") is True


def test_isbn_no_match():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788954681179", "9788935679188") is False


def test_isbn_non_standard_k_prefix():
    from yes24_scraper import isbn_matches
    assert isbn_matches("9788925588735", "K442137004") is False


def test_isbn_empty():
    from yes24_scraper import isbn_matches
    assert isbn_matches("", "9788954681179") is False
    assert isbn_matches("9788954681179", "") is False


def test_is_non_standard_isbn():
    from yes24_scraper import is_non_standard_isbn
    assert is_non_standard_isbn("K442137004") is True
    assert is_non_standard_isbn("12345") is True
    assert is_non_standard_isbn("9788954681179") is False


def test_build_search_query():
    from yes24_scraper import build_search_query
    assert build_search_query("데미안 (오리지널 초판본 표지디자인)", "헤르만 헤세 (지은이)") == "데미안 헤르만 헤세"


def test_build_search_query_multiple_authors():
    from yes24_scraper import build_search_query
    assert build_search_query("숨결이 바람 될 때", "폴 칼라니티, 이종인 (옮긴이)") == "숨결이 바람 될 때 폴 칼라니티"


def test_clean_section_text():
    from yes24_scraper import clean_section_text
    raw = "책소개\n좋은 책입니다.\n접기\n펼쳐보기"
    result = clean_section_text(raw)
    assert result == "좋은 책입니다."


def test_clean_section_text_too_short():
    from yes24_scraper import clean_section_text
    assert clean_section_text("짧음") is None


# ----- main() exit code 계약 -----

from unittest.mock import patch, MagicMock


def _run_main_with_stats(stats_dict):
    """main() 을 호출하되 Yes24Scraper 를 stub 으로 교체한다."""
    import sys
    argv_backup = sys.argv
    sys.argv = ["yes24_scraper.py"]
    try:
        with patch("yes24_scraper.Yes24Scraper") as FakeScraper:
            instance = MagicMock()
            instance.stats = stats_dict
            instance.run = MagicMock()
            FakeScraper.return_value = instance
            from yes24_scraper import main
            return main()
    finally:
        sys.argv = argv_backup


def test_main_exit_zero_when_nothing_processed():
    assert _run_main_with_stats({"processed": 0, "success": 0, "errors": 0}) == 0


def test_main_exit_one_when_total_annihilation():
    assert _run_main_with_stats({"processed": 50, "success": 0, "errors": 50}) == 1


def test_main_exit_one_when_success_below_50_percent():
    """100권 처리, 40권 성공 = 40% → exit 1."""
    assert _run_main_with_stats({"processed": 100, "success": 40, "errors": 60}) == 1


def test_main_exit_one_when_errors_exceed_success():
    """15권 성공, 20 errors → exit 1."""
    assert _run_main_with_stats({"processed": 35, "success": 15, "errors": 20}) == 1


def test_main_exit_zero_on_healthy_run():
    """90/100 성공, 10 errors → exit 0."""
    assert _run_main_with_stats({"processed": 100, "success": 90, "errors": 10}) == 0


def test_main_exit_zero_on_small_healthy_sample():
    """5권 처리, 3 성공 — 10권 미만이라 ratio 체크 스킵, errors(2) < success(3) → exit 0."""
    assert _run_main_with_stats({"processed": 5, "success": 3, "errors": 2}) == 0
