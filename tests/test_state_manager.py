from unittest.mock import MagicMock
from lib.state_manager import StateManager


def _make_manager():
    mock_sb = MagicMock()
    return StateManager(mock_sb), mock_sb


def test_reset_expired_states_calls_update():
    mgr, mock_sb = _make_manager()
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[{"id": "1"}])

    result = mgr.reset_expired_states(days=30)

    mock_sb.table.assert_called_with("batch_collection_state")
    mock_table.update.assert_called_once_with({"completed": False})
    assert result >= 0


def test_reset_skips_item_list():
    """Phase 1 (item_list)은 영구 완료 — 리셋 대상 아님"""
    mgr, mock_sb = _make_manager()
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])

    mgr.reset_expired_states(days=30)

    mock_table.neq.assert_called_with("source_type", "item_list")


def test_upsert_state_uses_iso_timestamp():
    """updated_at이 문자열 'now()'가 아닌 ISO 타임스탬프여야 함"""
    mgr, mock_sb = _make_manager()
    mock_table = MagicMock()
    mock_sb.table.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])

    mgr.upsert_state(source_type="item_list", query_type="Bestseller", category_id=1)

    call_args = mock_table.upsert.call_args[0][0]
    assert call_args["updated_at"] != "now()"
    assert "T" in call_args["updated_at"]  # ISO 포맷에는 T가 있음
