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
