from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class BookVectors:
    reasons: list[np.ndarray]
    desc: np.ndarray
    l1: np.ndarray
    l2: np.ndarray


class VectorIndex:
    """벡터 저장 + 검색 인덱스. 모든 벡터는 L2-정규화 가정."""

    def __init__(self, dim: int = 2000, dtype=np.float32):
        self.dim = dim
        self.dtype = dtype
        self._books: dict[str, BookVectors] = {}
        self._desc_matrix: Optional[np.ndarray] = None
        self._desc_bid_order: list[str] = []
        # bid → matrix index O(1) 조회용. build_desc_matrix 에서 채워지고
        # add_book 에서 invalidate 된다.
        self._desc_bid_to_idx: dict[str, int] = {}

    @property
    def book_ids(self) -> list[str]:
        return list(self._books.keys())

    def add_book(self, book_id: str, reasons: list[np.ndarray],
                 desc: np.ndarray, l1: np.ndarray, l2: np.ndarray):
        self._books[book_id] = BookVectors(
            reasons=[r.astype(self.dtype) for r in reasons],
            desc=desc.astype(self.dtype),
            l1=l1.astype(self.dtype),
            l2=l2.astype(self.dtype),
        )
        self._desc_matrix = None
        self._desc_bid_to_idx = {}

    def get_book(self, book_id: str) -> Optional[BookVectors]:
        return self._books.get(book_id)

    @staticmethod
    def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def build_desc_matrix(self):
        self._desc_bid_order = list(self._books.keys())
        descs = [self._books[bid].desc for bid in self._desc_bid_order]
        self._desc_matrix = np.stack(descs)
        self._desc_bid_to_idx = {
            bid: i for i, bid in enumerate(self._desc_bid_order)
        }

    def similar_by_vector(
        self,
        query_vec: np.ndarray,
        exclude_ids: Optional[set[str]] = None,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """임의 L2-정규화 query 벡터에 대해 전체 책을 스코어링하고
        exclude_ids를 제외한 top-K (book_id, score)를 반환."""
        if self._desc_matrix is None:
            self.build_desc_matrix()
        if exclude_ids is None:
            exclude_ids = set()
        scores = self._desc_matrix @ query_vec.astype(self.dtype)
        for ex in exclude_ids:
            idx = self._desc_bid_to_idx.get(ex)
            if idx is not None:
                scores[idx] = -999.0
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i])) for i in top_idx]

    def similar_by_desc(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        bv = self._books.get(book_id)
        if bv is None:
            return []
        return self.similar_by_vector(bv.desc, exclude_ids={book_id}, limit=limit)
