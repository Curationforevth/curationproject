from engine.utils import clean_html


def test_clean_html_strips_tags():
    assert clean_html("<p>안녕<br>세상</p>") == "안녕세상"


def test_clean_html_idempotent_on_plaintext():
    s = "태그 없는 평문"
    assert clean_html(s) == s


def test_clean_html_none_safe():
    assert clean_html(None) == ""
    assert clean_html("") == ""
