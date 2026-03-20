from lib.title_cleaner import clean_title


def test_removes_special_edition_paren():
    assert clean_title("채식주의자 (특별판)") == "채식주의자"


def test_removes_hardcover_paren():
    assert clean_title("소년이 온다 (양장)") == "소년이 온다"


def test_removes_goods_dash():
    assert clean_title("달러구트 꿈 백화점 - 포토카드 포함") == "달러구트 꿈 백화점"


def test_keeps_subtitle():
    assert clean_title("사피엔스 - 유인원에서 사이보그까지") == "사피엔스 - 유인원에서 사이보그까지"


def test_handles_empty_string():
    assert clean_title("") == ""


def test_removes_volume_info():
    assert clean_title("원피스 - 전105권") == "원피스"
