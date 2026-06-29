from reembed_provisional import plan_row_action


def test_relabel_without_reembed_when_text_same_tier_changed():
    # backfill 임시라벨 kakao_desc, 실제 minimal, source_text 불변 → 재임베딩 X, 라벨만 교정
    a = plan_row_action(stored_tier="kakao_desc", stored_source_text="제목 저자 소설",
                        new_text="제목 저자 소설", new_tier="minimal")
    assert a == {"reembed": False, "update_tier": True, "new_tier": "minimal"}


def test_reembed_when_text_changed_to_rich():
    a = plan_row_action(stored_tier="kakao_desc", stored_source_text="짧은 설명",
                        new_text="가" * 250, new_tier="rich")
    assert a == {"reembed": True, "update_tier": True, "new_tier": "rich"}


def test_noop_when_text_and_tier_same():
    a = plan_row_action(stored_tier="minimal", stored_source_text="제목 저자",
                        new_text="제목 저자", new_tier="minimal")
    assert a == {"reembed": False, "update_tier": False, "new_tier": "minimal"}


def test_reembed_but_same_tier_when_text_grew_same_tier():
    # description 이 길어졌지만 여전히 kakao_desc → 재임베딩(텍스트 변경), tier 불변
    a = plan_row_action(stored_tier="kakao_desc", stored_source_text="짧은 설명",
                        new_text="훨씬 길어진 카카오 설명 문단", new_tier="kakao_desc")
    assert a == {"reembed": True, "update_tier": False, "new_tier": "kakao_desc"}


def test_no_action_when_new_text_empty():
    # 텍스트가 사라진 비정상 케이스 → 아무것도 안 함(기존 보존)
    a = plan_row_action(stored_tier="minimal", stored_source_text="제목",
                        new_text=None, new_tier=None)
    assert a == {"reembed": False, "update_tier": False, "new_tier": "minimal"}
