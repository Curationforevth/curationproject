"""genre_parser 유닛 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.lib.genre_parser import parse_genre, clean_html


class TestParseGenre:
    def test_depth_4_standard(self):
        l1, l2 = parse_genre("국내도서>소설/시/희곡>한국소설>2000년대 이후 한국소설")
        assert l1 == "소설/시/희곡"
        assert l2 == "한국소설 2000년대 이후 한국소설"

    def test_depth_3(self):
        l1, l2 = parse_genre("국내도서>경제경영>재테크/투자")
        assert l1 == "경제경영"
        assert l2 == "재테크/투자"

    def test_depth_5(self):
        l1, l2 = parse_genre("국내도서>어린이>과학/수학/컴퓨터>지구와 우주>태양계")
        assert l1 == "어린이"
        assert l2 == "과학/수학/컴퓨터 지구와 우주 태양계"

    def test_foreign_prefix(self):
        l1, l2 = parse_genre("외국도서>소설/시/희곡>영미소설")
        assert l1 == "소설/시/희곡"
        assert l2 == "영미소설"

    def test_ebook_prefix(self):
        l1, l2 = parse_genre("eBook>인문학>철학")
        assert l1 == "인문학"
        assert l2 == "철학"

    def test_empty_string(self):
        l1, l2 = parse_genre("")
        assert l1 is None
        assert l2 is None

    def test_none(self):
        l1, l2 = parse_genre(None)
        assert l1 is None
        assert l2 is None

    def test_depth_2_no_l2(self):
        l1, l2 = parse_genre("국내도서>경제경영")
        assert l1 == "경제경영"
        assert l2 is None

    def test_no_known_prefix(self):
        l1, l2 = parse_genre("해외도서>소설")
        assert l1 == "해외도서"
        assert l2 == "소설"


class TestCleanHtml:
    def test_removes_tags(self):
        assert clean_html("<p>hello</p>") == "hello"

    def test_nested_tags(self):
        assert clean_html("<div><b>bold</b> text</div>") == "bold text"

    def test_empty(self):
        assert clean_html("") == ""

    def test_none(self):
        assert clean_html(None) == ""

    def test_no_tags(self):
        assert clean_html("plain text") == "plain text"

    def test_whitespace_only_after_clean(self):
        assert clean_html("<p>  </p>").strip() == ""
