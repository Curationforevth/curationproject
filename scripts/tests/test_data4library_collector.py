"""정보나루 수집기 순수 함수 테스트"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestParseKeywords:
    """API 응답에서 키워드 파싱"""

    def test_parse_keywords_normal(self):
        from data4library_collector import parse_keywords
        response = {
            "response": {
                "keywords": [
                    {"keyword": {"word": "인생"}},
                    {"keyword": {"word": "성장"}},
                    {"keyword": {"word": "자아찾기"}},
                ]
            }
        }
        assert parse_keywords(response) == ["인생", "성장", "자아찾기"]

    def test_parse_keywords_empty(self):
        from data4library_collector import parse_keywords
        assert parse_keywords({}) == []
        assert parse_keywords({"response": {}}) == []
        assert parse_keywords(None) == []

    def test_parse_keywords_missing_word(self):
        from data4library_collector import parse_keywords
        response = {
            "response": {
                "keywords": [
                    {"keyword": {"word": "인생"}},
                    {"keyword": {}},
                ]
            }
        }
        assert parse_keywords(response) == ["인생"]


class TestParseCoLoanBooks:
    """API 응답에서 함께 빌린 책 ISBN 파싱"""

    def test_parse_co_loan_normal(self):
        from data4library_collector import parse_co_loan_books
        response = {
            "response": {
                "coLoanBooks": [
                    {"book": {"isbn13": "9788932920993"}},
                    {"book": {"isbn13": "9788936434120"}},
                ]
            }
        }
        assert parse_co_loan_books(response) == ["9788932920993", "9788936434120"]

    def test_parse_co_loan_empty(self):
        from data4library_collector import parse_co_loan_books
        assert parse_co_loan_books({}) == []
        assert parse_co_loan_books(None) == []

    def test_parse_co_loan_capped_at_50(self):
        from data4library_collector import parse_co_loan_books
        books = [{"book": {"isbn13": f"978893292{i:04d}"}} for i in range(80)]
        response = {"response": {"coLoanBooks": books}}
        result = parse_co_loan_books(response)
        assert len(result) == 50

    def test_parse_co_loan_missing_isbn(self):
        from data4library_collector import parse_co_loan_books
        response = {
            "response": {
                "coLoanBooks": [
                    {"book": {"isbn13": "9788932920993"}},
                    {"book": {}},
                    {"book": {"isbn13": "9788936434120"}},
                ]
            }
        }
        assert parse_co_loan_books(response) == ["9788932920993", "9788936434120"]
