from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np

from config import SOURCE_TIER_PENALTY, SIMILAR_MIN_TIER

# 낮음→높음. SIMILAR_MIN_TIER 미만 tier 는 /similar 결과에서 제외(정밀 surface 보호).
_TIER_ORDER = ["minimal", "kakao_desc", "rich"]


def _below_similar_min(tier: str) -> bool:
    return _TIER_ORDER.index(tier) < _TIER_ORDER.index(SIMILAR_MIN_TIER)


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
        # 후보 품질 등급(non-rich 만, sparse). build_index 가 채워 pkl 에 직렬화.
        # 부재(구 pkl) 시 getattr 폴백 → 무감점. _penalty_vec/_exclude_similar 는 파생.
        self._candidate_tier: dict[str, str] = {}
        self._penalty_vec: Optional[np.ndarray] = None
        self._exclude_similar: set[str] = set()

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

    def strip_unused_genre_vectors(self):
        """모든 책의 l1/l2 를 단일 공유 zero 벡터로 치환해 메모리를 회수한다.

        W_L1=W_L2=0(config) 이라 l1/l2 는 스코어링에서 절대 안 쓰임(dead). 책당 l1+l2
        (f16 2000d ≈ 8KB)가 인덱스에 상주 → N권이면 수십 MB 낭비. 단일 zero 공유로
        ~0 으로 줄여 무료 512MB OOM 을 완화한다(스코어 결과 불변: 곱해지는 가중치가 0).
        load 직후 호출하면 재빌드 없이 현재 인덱스에도 즉시 적용된다.
        """
        z = np.zeros(self.dim, dtype=self.dtype)
        for bv in self._books.values():
            bv.l1 = z
            bv.l2 = z

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
        # source_tier 파생: 후보별 페널티 벡터(_desc_bid_order 정렬) + /similar 제외 집합.
        # getattr 폴백으로 구 pkl(_candidate_tier 없음) 안전 → 전부 rich(1.0)·제외 없음.
        tier = getattr(self, "_candidate_tier", {})
        self._penalty_vec = np.array(
            [SOURCE_TIER_PENALTY[tier.get(bid, "rich")] for bid in self._desc_bid_order],
            dtype=np.float32,
        )
        self._exclude_similar = {b for b, t in tier.items() if _below_similar_min(t)}

    def attach_desc_matrix(self, matrix, bid_order):
        """외부에서 만든 desc 행렬을 그대로 _desc_matrix 로 붙인다(중복 빌드/저장 회피).

        build_desc_matrix 는 per-book BookVectors.desc 로 새 행렬을 또 만들어 desc 를
        3중(per-book + 이 행렬 + 번들)으로 보유 → 무료 512MB 초과. desc 를 per-book 에서
        strip 하고 번들 desc_matrix 하나만 attach 하면 desc 1벌(~72MB 절감). matrix 행 순서는
        bid_order 와 일치해야 한다(빌드가 같은 순서로 생성). penalty/exclude 도 여기서 파생.
        """
        self._desc_matrix = matrix
        self._desc_bid_order = list(bid_order)
        self._desc_bid_to_idx = {bid: i for i, bid in enumerate(self._desc_bid_order)}
        tier = getattr(self, "_candidate_tier", {})
        self._penalty_vec = np.array(
            [SOURCE_TIER_PENALTY[tier.get(bid, "rich")] for bid in self._desc_bid_order],
            dtype=np.float32,
        )
        self._exclude_similar = {b for b, t in tier.items() if _below_similar_min(t)}

    def desc_of(self, book_id: str):
        """책의 desc 벡터. per-book 에 있으면 그걸, strip 됐으면 _desc_matrix 에서 조회."""
        bv = self._books.get(book_id)
        if bv is not None and bv.desc is not None:
            return bv.desc
        i = self._desc_bid_to_idx.get(book_id)
        if i is not None and self._desc_matrix is not None:
            return self._desc_matrix[i]
        return None

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
        exclude_ids = set(exclude_ids or set()) | getattr(self, "_exclude_similar", set())
        scores = self._desc_matrix @ query_vec.astype(self.dtype)
        # source_tier down-weight (positive-part — 음수 코사인 미변경, 부호 안전).
        pv = getattr(self, "_penalty_vec", None)
        if pv is not None:
            scores = np.where(scores > 0, scores * pv, scores)
        for ex in exclude_ids:
            idx = self._desc_bid_to_idx.get(ex)
            if idx is not None:
                scores[idx] = -999.0
        top_idx = np.argsort(scores)[::-1][:limit]
        return [(self._desc_bid_order[i], float(scores[i]))
                for i in top_idx if scores[i] > -900.0]

    def similar_by_desc(self, book_id: str, limit: int = 10) -> list[tuple[str, float]]:
        bv = self._books.get(book_id)
        if bv is None:
            return []
        return self.similar_by_vector(bv.desc, exclude_ids={book_id}, limit=limit)
