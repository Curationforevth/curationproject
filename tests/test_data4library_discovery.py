"""Discovery collector — pure logic 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.data4library_discovery_collector import (
    KDC_BUCKETS,
    dedup_in_batch_by_isbn,
    sanitize_for_upsert,
    extract_first_author,
)


def test_kdc_buckets_cover_main_genres():
    keys = {b["kdc"] for b in KDC_BUCKETS}
    assert "8" in keys  # 문학
    assert "1" in keys  # 철학
    assert "3" in keys  # 사회과학
    assert "9" in keys  # 역사


def test_dedup_in_batch_by_isbn_keeps_highest_loan_count():
    rows = [
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 100},
        {"isbn13": "9788936434120", "title": "소년이 온다", "loan_count": 250},
        {"isbn13": "9788954682152", "title": "작별하지 않는다", "loan_count": 200},
    ]
    out = dedup_in_batch_by_isbn(rows)
    assert len(out) == 2
    by_isbn = {r["isbn13"]: r for r in out}
    assert by_isbn["9788936434120"]["loan_count"] == 250
    assert by_isbn["9788954682152"]["loan_count"] == 200


def test_extract_first_author_strips_role_prefix():
    assert extract_first_author("지은이: 한강") == "한강"
    assert extract_first_author("저자: 유발 하라리 ;옮긴이: 조현욱") == "유발 하라리"
    assert extract_first_author("글: 최설희 ;그림: 한현동") == "최설희"
    assert extract_first_author("한강") == "한강"
    assert extract_first_author("") == ""
    assert extract_first_author(None) == ""


def test_sanitize_for_upsert_maps_columns():
    parsed = {
        "isbn13": "9788936434120",
        "title": "소년이 온다 :한강 장편소설",
        "author_raw": "지은이: 한강",
        "publisher": "창비",
        "publication_year": "2014",
        "addition_symbol": "03810",
        "kdc": "813.62",
        "cover_url": "http://example.com/cover.jpg",
        "loan_count": 3699,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9788936434120"
    assert row["title"] == "소년이 온다 :한강 장편소설"
    assert row["author"] == "한강"
    assert row["publisher"] == "창비"
    assert row["cover_url"] == "http://example.com/cover.jpg"
    assert row["loan_count"] == 3699
    assert row["sales_point"] == 3699
    assert "isbn13" not in row
    assert "kdc" not in row
    assert "addition_symbol" not in row
    assert "publication_year" not in row
    assert "author_raw" not in row


def test_sanitize_for_upsert_handles_missing_optional():
    parsed = {
        "isbn13": "9999999999999",
        "title": "x",
        "author_raw": "",
        "publisher": None,
        "publication_year": None,
        "addition_symbol": "",
        "kdc": None,
        "cover_url": None,
        "loan_count": 0,
    }
    row = sanitize_for_upsert(parsed)
    assert row["isbn"] == "9999999999999"
    assert row["author"] == ""
    assert row["loan_count"] == 0


from scripts.data4library_discovery_collector import (
    select_seed_isbns_for_tier2,
)


def test_select_seed_isbns_for_tier2_picks_top_n_by_loan_count():
    rows = [
        {"isbn13": "isbn1", "loan_count": 500},
        {"isbn13": "isbn2", "loan_count": 1500},
        {"isbn13": "isbn3", "loan_count": 100},
        {"isbn13": "isbn4", "loan_count": 800},
    ]
    seeds = select_seed_isbns_for_tier2(rows, top_n=2)
    assert seeds == ["isbn2", "isbn4"]


def test_select_seed_isbns_for_tier2_skips_blank_isbn():
    rows = [
        {"isbn13": "", "loan_count": 9999},
        {"isbn13": "isbn2", "loan_count": 100},
    ]
    seeds = select_seed_isbns_for_tier2(rows, top_n=5)
    assert seeds == ["isbn2"]


def test_select_seed_isbns_for_tier2_empty():
    assert select_seed_isbns_for_tier2([], top_n=10) == []


from scripts.data4library_discovery_collector import (
    filter_single_token_keywords,
)


def test_filter_single_token_keywords_keeps_single_words():
    keywords = [
        ("사랑", 48.354),
        ("나태주 시집", 25.328),
        ("인생", 25.328),
        ("풀꽃", 20.723),
    ]
    out = filter_single_token_keywords(keywords)
    assert ("사랑", 48.354) in out
    assert ("인생", 25.328) in out
    assert ("풀꽃", 20.723) in out
    assert all(" " not in w for w, _ in out)
    assert len(out) == 3


def test_filter_single_token_keywords_drops_too_short_words():
    keywords = [("사", 99.0), ("사랑", 50.0)]
    out = filter_single_token_keywords(keywords)
    assert ("사", 99.0) not in out
    assert ("사랑", 50.0) in out


def test_filter_single_token_keywords_dedupes():
    keywords = [("사랑", 50.0), ("사랑", 30.0)]
    out = filter_single_token_keywords(keywords)
    assert len(out) == 1
    assert out[0][0] == "사랑"


from unittest.mock import patch, MagicMock

from scripts.data4library_discovery_collector import (
    trigger_enrich_pipeline,
)


def test_trigger_enrich_pipeline_calls_orchestrator_as_subprocess():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = trigger_enrich_pipeline(dry_run=False)
    assert code == 0
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert "scripts/pipeline_orchestrator.py" in " ".join(cmd)
    assert "--dry-run" not in cmd


def test_trigger_enrich_pipeline_passes_dry_run():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        trigger_enrich_pipeline(dry_run=True)
    cmd = mock_run.call_args[0][0]
    assert "--dry-run" in cmd


def test_trigger_enrich_pipeline_returns_nonzero_on_failure():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        code = trigger_enrich_pipeline(dry_run=False)
    assert code == 1


def test_trigger_enrich_pipeline_passes_limit():
    with patch("scripts.data4library_discovery_collector.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        trigger_enrich_pipeline(dry_run=False, limit=20)
    cmd = mock_run.call_args[0][0]
    assert "--limit" in cmd
    assert "20" in cmd


# ============================================================
# KI-004: Tier 2 시드를 books DB 에서 직접 조회 (fetch_tier1 재호출 제거)
# ============================================================

def test_fetch_tier2_seeds_from_db_returns_isbns():
    """KI-004: books 테이블에서 loan_count desc 로 top-N ISBN 반환."""
    from scripts.data4library_discovery_collector import DiscoveryCollector

    c = DiscoveryCollector(dry_run=True)
    c._sb = MagicMock()

    fake_result = MagicMock()
    fake_result.data = [
        {"isbn": "9781111111111"},
        {"isbn": "9782222222222"},
        {"isbn": "9783333333333"},
    ]
    # supabase 체이닝 mock — 마지막 execute() 만 결과 반환하면 됨
    c.sb.table.return_value.select.return_value.not_.is_.return_value \
        .not_.is_.return_value.order.return_value.limit.return_value \
        .execute.return_value = fake_result

    seeds = c.fetch_tier2_seeds_from_db(top_n=3)
    assert seeds == ["9781111111111", "9782222222222", "9783333333333"]


def test_fetch_tier2_seeds_from_db_skips_blank_isbns():
    """ISBN 누락된 row 는 결과에서 빠진다."""
    from scripts.data4library_discovery_collector import DiscoveryCollector

    c = DiscoveryCollector(dry_run=True)
    c._sb = MagicMock()

    fake_result = MagicMock()
    fake_result.data = [
        {"isbn": "9781111111111"},
        {"isbn": ""},
        {"isbn": None},
        {"isbn": "9782222222222"},
    ]
    c.sb.table.return_value.select.return_value.not_.is_.return_value \
        .not_.is_.return_value.order.return_value.limit.return_value \
        .execute.return_value = fake_result

    seeds = c.fetch_tier2_seeds_from_db(top_n=10)
    assert seeds == ["9781111111111", "9782222222222"]


def test_fetch_tier2_seeds_from_db_empty_when_no_books():
    from scripts.data4library_discovery_collector import DiscoveryCollector

    c = DiscoveryCollector(dry_run=True)
    c._sb = MagicMock()

    fake_result = MagicMock()
    fake_result.data = []
    c.sb.table.return_value.select.return_value.not_.is_.return_value \
        .not_.is_.return_value.order.return_value.limit.return_value \
        .execute.return_value = fake_result

    seeds = c.fetch_tier2_seeds_from_db(top_n=10)
    assert seeds == []
