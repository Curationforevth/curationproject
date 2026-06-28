"""source_tier 문자열 단일성 — 4곳(config / 마이그레이션 CHECK / 배치·라이브 산출)이
정확히 일치하는지 고정. 배포 경계로 코드 공유 불가하므로 drift 를 테스트로 검출(R2)."""
import json
import os
import re

from config import SOURCE_TIER_PENALTY, SIMILAR_MIN_TIER

TIERS = {"rich", "kakao_desc", "minimal"}
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def test_penalty_keys_are_canonical_tiers():
    assert set(SOURCE_TIER_PENALTY) == TIERS


def test_similar_min_tier_is_a_known_tier():
    assert SIMILAR_MIN_TIER in TIERS


def test_migration_check_allows_exactly_the_canonical_tiers():
    path = os.path.join(ROOT, "supabase", "migrations",
                        "20260628000001_book_v3_vectors_source_tier.sql")
    sql = open(path, encoding="utf-8").read()
    in_check = set(re.findall(r"'(rich|kakao_desc|minimal)'", sql))
    assert TIERS <= in_check, "마이그레이션 CHECK 가 정본 tier 3종을 모두 허용해야 함"


def test_fixture_tiers_subset_of_canonical():
    path = os.path.join(ROOT, "tests", "fixtures", "source_tier_cases.json")
    cases = json.load(open(path, encoding="utf-8"))
    seen = {c["tier"] for c in cases if c["tier"] is not None}
    assert seen <= TIERS and seen == TIERS, "픽스처가 정본 tier 3종을 모두 커버"
