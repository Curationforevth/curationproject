"""tier2_embedder 유닛 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestParseSection:
    """rich_description에서 섹션 추출"""

    def test_parse_intro(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n이 책은 멋진 소설이다.\n\n[출판사리뷰]\n마케팅 문구"
        assert parse_section(rd, '책소개') == "이 책은 멋진 소설이다."

    def test_parse_excerpt(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n소개글\n\n[책속으로]\n나는 그날 밤을 기억한다."
        assert parse_section(rd, '책속으로') == "나는 그날 밤을 기억한다."

    def test_parse_missing_section(self):
        from tier2_embedder import parse_section
        rd = "[책소개]\n소개글\n\n[출판사리뷰]\n리뷰"
        assert parse_section(rd, '책속으로') == ""

    def test_parse_empty_rd(self):
        from tier2_embedder import parse_section
        assert parse_section("", '책소개') == ""
        assert parse_section(None, '책소개') == ""


class TestCleanIntro:
    """책소개 노이즈 제거"""

    def test_remove_stars(self):
        from tier2_embedder import clean_intro
        text = "★★★ 영화 개봉\n★ 특별판\n이 소설은 좋다."
        assert "★" not in clean_intro(text)
        assert "이 소설은 좋다." in clean_intro(text)

    def test_remove_md_comment(self):
        from tier2_embedder import clean_intro
        text = "MD 한마디\n좋은 책입니다.\n2025.06.20.\n소설 PD 김유리\n\n진짜 내용이다."
        result = clean_intro(text)
        assert "MD 한마디" not in result
        assert "좋은 책입니다." not in result
        assert "진짜 내용이다." in result

    def test_remove_preview(self):
        from tier2_embedder import clean_intro
        text = "이 소설은 좋다.\n 책의 일부 내용을 미리 읽어보실 수 있습니다. 미리보기"
        result = clean_intro(text)
        assert "미리보기" not in result
        assert "이 소설은 좋다." in result

    def test_clean_text_passthrough(self):
        from tier2_embedder import clean_intro
        text = "1980년대 서울을 배경으로 한 소설이다."
        assert clean_intro(text) == text


class TestComposeEmbedding:
    """점진적 텍스트 조합"""

    def test_aladin_only(self):
        """rich_description 없는 책은 빈 결과"""
        from tier2_embedder import compose_embedding
        book = {'title': '제목', 'author': '저자', 'genre': '소설', 'description': '설명', 'rich_description': None}
        text, sources = compose_embedding(book)
        assert text == ""
        assert sources == []

    def test_with_intro_only(self):
        """책소개만 있는 경우"""
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '알라딘 설명',
            'rich_description': '[책소개]\n멋진 소설이다.\n\n[출판사리뷰]\n마케팅',
        }
        text, sources = compose_embedding(book)
        assert '제목: 테스트책' in text
        assert '저자: 저자A' in text
        assert '장르: 한국소설' in text
        assert '내용: 알라딘 설명' in text
        assert '책소개: 멋진 소설이다.' in text
        assert 'aladin' in sources
        assert 'yes24_intro' in sources
        assert 'yes24_excerpt' not in sources

    def test_with_intro_and_excerpt(self):
        """책소개 + 책속으로"""
        from tier2_embedder import compose_embedding
        book = {
            'title': '테스트책', 'author': '저자A', 'genre': '한국소설',
            'description': '설명',
            'rich_description': '[책소개]\n멋진 소설이다.\n\n[책속으로]\n나는 그날을 기억한다.',
        }
        text, sources = compose_embedding(book)
        assert '책소개: 멋진 소설이다.' in text
        assert '발췌: 나는 그날을 기억한다.' in text
        assert 'yes24_intro' in sources
        assert 'yes24_excerpt' in sources

    def test_excerpt_truncated_to_300(self):
        """책속으로는 300자로 잘림"""
        from tier2_embedder import compose_embedding
        long_excerpt = "가" * 500
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': f'[책소개]\n소개\n\n[책속으로]\n{long_excerpt}',
        }
        text, _ = compose_embedding(book)
        excerpt_part = text.split('발췌: ')[1]
        assert len(excerpt_part) == 300

    def test_no_intro_returns_empty(self):
        """책소개 파싱 실패 시 빈 결과 (Tier 2 최소 조건 미달)"""
        from tier2_embedder import compose_embedding
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': '[출판사리뷰]\n마케팅만 있음',
        }
        text, sources = compose_embedding(book)
        assert text == ""
        assert sources == []

    def test_truncate_long_text(self):
        """15000자(~7500토큰) 초과 시 잘림"""
        from tier2_embedder import compose_embedding
        long_intro = "가" * 20000
        book = {
            'title': 'T', 'author': 'A', 'genre': 'G', 'description': 'D',
            'rich_description': f'[책소개]\n{long_intro}',
        }
        text, _ = compose_embedding(book)
        assert len(text) <= 15000
