"""reembed_provisional 판정 — 2026-08-27 부터 원문 대신 sha256 을 비교한다.

book_v3_vectors.source_text 전문(13.2MB)은 이 동등 비교 하나에만 쓰였다.
Free DB 가 한계라 해시로 대체했다 — 해시 동등 ⟺ 원문 동등이라 의미는 같다.
사전 실측: 전 행 max(length(source_text))=2000, 2000 초과 0행. 즉 저장 경로들이
이미 _pick_source_text 의 [:2000] 로 일치해 있어 해시 전환이 대량 재임베딩을
유발하지 않는다.
"""
from reembed_provisional import plan_row_action, source_sha


def test_relabel_without_reembed_when_text_same_tier_changed():
    # backfill 임시라벨 kakao_desc, 실제 minimal, source_text 불변 → 재임베딩 X, 라벨만 교정
    a = plan_row_action(stored_tier="kakao_desc",
                        stored_source_sha=source_sha("제목 저자 소설"),
                        new_text="제목 저자 소설", new_tier="minimal")
    assert a == {"reembed": False, "update_tier": True, "new_tier": "minimal"}


def test_reembed_when_text_changed_to_rich():
    a = plan_row_action(stored_tier="kakao_desc",
                        stored_source_sha=source_sha("짧은 설명"),
                        new_text="가" * 250, new_tier="rich")
    assert a == {"reembed": True, "update_tier": True, "new_tier": "rich"}


def test_noop_when_text_and_tier_same():
    a = plan_row_action(stored_tier="minimal",
                        stored_source_sha=source_sha("제목 저자"),
                        new_text="제목 저자", new_tier="minimal")
    assert a == {"reembed": False, "update_tier": False, "new_tier": "minimal"}


def test_reembed_but_same_tier_when_text_grew_same_tier():
    # description 이 길어졌지만 여전히 kakao_desc → 재임베딩(텍스트 변경), tier 불변
    a = plan_row_action(stored_tier="kakao_desc",
                        stored_source_sha=source_sha("짧은 설명"),
                        new_text="훨씬 길어진 카카오 설명 문단", new_tier="kakao_desc")
    assert a == {"reembed": True, "update_tier": False, "new_tier": "kakao_desc"}


def test_no_action_when_new_text_empty():
    # 텍스트가 사라진 비정상 케이스 → 아무것도 안 함(기존 보존)
    a = plan_row_action(stored_tier="minimal", stored_source_sha=source_sha("제목"),
                        new_text=None, new_tier=None)
    assert a == {"reembed": False, "update_tier": False, "new_tier": "minimal"}


# ── 해시 전환 자체의 계약 ──────────────────────────────────────────────

def test_source_sha_is_stable_and_64_hex():
    h = source_sha("동일한 문장")
    assert h == source_sha("동일한 문장")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_source_sha_differs_on_change():
    assert source_sha("가나다") != source_sha("가나라")


def test_sha_backfill_matches_postgres_encoding():
    """마이그레이션의 encode(sha256(text::bytea),'hex') 와 동일해야 한다.

    다르면 첫 run 에 전 행이 '변경됨' 으로 판정돼 대량 재임베딩(비용)이 터진다.
    """
    import hashlib
    s = "테스트 원문 abc"
    assert source_sha(s) == hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_stored_sha_missing_triggers_reembed():
    """해시가 아직 없는 행(백필 누락 등)은 재임베딩으로 복구된다 — 조용히 skip 금지."""
    a = plan_row_action(stored_tier="minimal", stored_source_sha=None,
                        new_text="제목 저자", new_tier="minimal")
    assert a["reembed"] is True
