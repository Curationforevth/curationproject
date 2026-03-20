import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_compose_embedding_text():
    """title + author + genre + description을 조합"""
    from tier1_embedder import compose_embedding_text

    book = {
        "title": "채식주의자",
        "author": "한강",
        "genre": "소설/시/희곡",
        "description": "한강의 연작소설. 채식을 시작한 여자의 이야기.",
    }
    text = compose_embedding_text(book)
    assert "채식주의자" in text
    assert "한강" in text
    assert "소설" in text
    assert "채식을 시작한" in text


def test_compose_embedding_text_empty_description():
    """description이 없어도 동작"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": "저자", "genre": "소설", "description": ""}
    text = compose_embedding_text(book)
    assert "제목" in text
    assert len(text) > 0


def test_compose_embedding_text_none_fields():
    """None 필드 처리"""
    from tier1_embedder import compose_embedding_text

    book = {"title": "제목", "author": None, "genre": None, "description": None}
    text = compose_embedding_text(book)
    assert "제목" in text
