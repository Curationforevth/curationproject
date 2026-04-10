"""YES24 스크래퍼 단위 테스트 — 검색 쿼리 생성 + 제목 정규화."""
from scripts.yes24_scraper import build_search_query, normalize_for_match


# --- build_search_query ---

def test_build_search_query_basic():
    assert build_search_query("꿈 목욕", "김지연") == "꿈 목욕 김지연"


def test_build_search_query_removes_colon_subtitle():
    assert build_search_query("파친코 :이민진 장편소설", "이민진 지음") == "파친코 이민진"


def test_build_search_query_removes_equals_subtitle():
    q = build_search_query(
        "매스커레이드 호텔 =히가시노 게이고 장편소설 /Masquerade hotel",
        "히가시노 게이고",
    )
    assert q == "매스커레이드 호텔 히가시노 게이고"


def test_build_search_query_removes_dash_subtitle():
    assert build_search_query("여덟 단어 :인생을 대하는 우리의 자세", "박웅현 지음") == "여덟 단어 박웅현"


def test_build_search_query_removes_author_suffix():
    assert build_search_query("미 비포 유", "조조 모예스 지음") == "미 비포 유 조조 모예스"


def test_build_search_query_removes_paren_author():
    q = build_search_query(
        "예루살렘의 아이히만 (알라딘 리커버 특별판)",
        "한나 아렌트 (지은이), 김선욱 (옮긴이)",
    )
    assert q == "예루살렘의 아이히만 한나 아렌트"


def test_build_search_query_removes_series_number():
    assert build_search_query("스파이 패밀리 16", "") == "스파이 패밀리"


def test_build_search_query_preserves_1984():
    """숫자만 있는 제목은 시리즈 번호로 잘못 제거되면 안 됨."""
    assert build_search_query("1984", "") == "1984"


def test_build_search_query_preserves_time_colon():
    """시간 표기 12:00의 콜론은 분리하면 안 됨."""
    assert build_search_query("12:00의 약속", "") == "12:00의 약속"


def test_build_search_query_korean_colon_no_space():
    """한글:한글 패턴은 분리."""
    assert build_search_query("고래:천명관 장편소설", "천명관 지음") == "고래 천명관"


def test_build_search_query_slash_separator():
    assert build_search_query("싸드 =김진명 장편소설 /THAAD", "김진명") == "싸드 김진명"


def test_build_search_query_empty_author():
    assert build_search_query("한국단편소설 40", "") == "한국단편소설"


def test_build_search_query_multiple_suffixes():
    """옮김 suffix도 제거."""
    assert build_search_query("노인과 바다", "어니스트 헤밍웨이 지음, 이인규 옮김") == "노인과 바다 어니스트 헤밍웨이"


# --- normalize_for_match ---

def test_normalize_removes_spaces():
    assert normalize_for_match("해리 포터와 마법사의 돌") == "해리포터와마법사의돌"


def test_normalize_removes_paren():
    assert normalize_for_match("예루살렘의 아이히만 (알라딘 리커버 특별판)") == "예루살렘의아이히만"


def test_normalize_removes_set_suffix():
    assert normalize_for_match("해리 포터와 마법사의 돌 1~2권 세트") == "해리포터와마법사의돌"


def test_normalize_preserves_1984():
    assert normalize_for_match("1984") == "1984"


def test_normalize_cross_edition_match():
    """알라딘 리커버판과 YES24 일반판이 동일 정규화."""
    db = normalize_for_match("예루살렘의 아이히만 (알라딘 리커버 특별판)")
    page = normalize_for_match("예루살렘의 아이히만")
    assert db == page


def test_normalize_space_difference_match():
    """띄어쓰기 차이가 있어도 매칭."""
    db = normalize_for_match("해리포터와 마법사의 돌")
    page = normalize_for_match("해리 포터와 마법사의 돌")
    assert db == page
