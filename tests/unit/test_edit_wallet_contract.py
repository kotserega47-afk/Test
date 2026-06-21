from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from automation.edit_wallet_contract import (
    ExcelRouting,
    RESULT_FAIL_INVALID,
    detect_excel_routing,
    prepare_edit_wallet_batch,
)


def _write_xlsx(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    df = pd.DataFrame(rows, columns=columns)
    df.to_excel(path, index=False)


def test_edit_wallet_filename_routes_to_edit_wallet(tmp_path):
    path = tmp_path / "edit_wallet_batch.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "status": "Тест"}])
    routing, error = detect_excel_routing(str(path), original_filename=path.name)
    assert routing == ExcelRouting.EDIT_WALLET
    assert error is None


def test_edit_wallet_filename_contains_edit_wallet(tmp_path):
    path = tmp_path / "my_edit_wallet_v1.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "phone": "79491103311"}])
    routing, _ = detect_excel_routing(str(path), original_filename=path.name)
    assert routing == ExcelRouting.EDIT_WALLET


def test_card_required_for_edit_wallet(tmp_path):
    path = tmp_path / "edit_wallet_bad.xlsx"
    _write_xlsx(path, [{"status": "Тест"}])
    routing, error = detect_excel_routing(str(path), original_filename=path.name)
    assert routing == ExcelRouting.AMBIGUOUS
    assert error and "card" in error


def test_action_value_forbidden_in_edit_wallet(tmp_path):
    path = tmp_path / "edit_wallet_disable.xlsx"
    _write_xlsx(
        path,
        [{"card": "9990110810347534", "action": "remove_partner", "value": "P1", "status": "Тест"}],
    )
    routing, error = detect_excel_routing(str(path), original_filename=path.name)
    assert routing == ExcelRouting.AMBIGUOUS
    assert error and "action/value" in error


def test_prepare_rejects_action_value(tmp_path):
    path = tmp_path / "edit_wallet_bad.xlsx"
    _write_xlsx(
        path,
        [{"card": "9990110810347534", "action": "remove_partner", "value": "P1"}],
    )
    with pytest.raises(ValueError, match="action/value"):
        prepare_edit_wallet_batch(str(path))


def test_row_with_only_card_is_invalid(tmp_path):
    path = tmp_path / "edit_wallet_only_card.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534"}])
    batch = prepare_edit_wallet_batch(str(path))
    assert batch.rows == []
    assert len(batch.invalid_rows) == 1
    assert batch.invalid_rows[0].result == RESULT_FAIL_INVALID
    assert "нет полей" in batch.invalid_rows[0].comment


def test_empty_cells_excluded_from_provided_columns(tmp_path):
    path = tmp_path / "edit_wallet_sparse.xlsx"
    _write_xlsx(
        path,
        [{"card": "9990110810347534", "phone": "", "status": "Тест", "comment": ""}],
    )
    batch = prepare_edit_wallet_batch(str(path))
    assert len(batch.rows) == 1
    row = batch.rows[0]
    assert row.provided_columns == frozenset({"status"})
    assert "phone" not in row.provided_columns
    assert "comment" not in row.provided_columns


def test_duplicate_card_invalid(tmp_path):
    path = tmp_path / "edit_wallet_dup.xlsx"
    _write_xlsx(
        path,
        [
            {"card": "9990110810347534", "status": "Тест"},
            {"card": "9990110810347534", "phone": "79491103311"},
        ],
    )
    batch = prepare_edit_wallet_batch(str(path))
    assert len(batch.rows) == 1
    assert len(batch.invalid_rows) == 1
    assert batch.invalid_rows[0].result == RESULT_FAIL_INVALID
    assert "дубликат" in batch.invalid_rows[0].comment


def test_invalid_status_rejected(tmp_path):
    path = tmp_path / "edit_wallet_bad_status.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "status": "NOT_A_STATUS"}])
    batch = prepare_edit_wallet_batch(str(path))
    assert batch.rows == []
    assert batch.invalid_rows[0].result == RESULT_FAIL_INVALID
    assert "status" in batch.invalid_rows[0].comment


def test_disable_routing_delegated(tmp_path):
    path = tmp_path / "disable.xlsx"
    _write_xlsx(path, [{"card": "4111", "action": "remove_partner", "value": "x"}])
    routing, _ = detect_excel_routing(str(path))
    assert routing == ExcelRouting.DISABLE


def test_add_wallet_routing_delegated(tmp_path):
    path = tmp_path / "add.xlsx"
    _write_xlsx(path, [{"card": "4111", "phone": "79491103311"}])
    routing, _ = detect_excel_routing(str(path))
    assert routing == ExcelRouting.ADD_WALLET
