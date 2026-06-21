from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from automation.edit_wallet_contract import (
    ExcelRouting,
    EditWalletRow,
    RESULT_FAIL_INVALID,
    build_success_comment,
    detect_excel_routing,
    is_clear_marker,
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
    assert row.cleared_columns == frozenset()
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CLEAR", True),
        ("clear", True),
        ("Clear", True),
        ("cLeAr", True),
        (" CLEAR ", True),
        ("CLEARING", False),
        ("please clear", False),
        ("=CLEAR", False),
        ("", False),
    ],
)
def test_is_clear_marker(raw, expected):
    assert is_clear_marker(raw) is expected


def test_clear_marker_in_cleared_columns_not_provided(tmp_path):
    path = tmp_path / "edit_wallet_clear.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "comment": "CLEAR"}])
    batch = prepare_edit_wallet_batch(str(path))
    row = batch.rows[0]
    assert row.cleared_columns == frozenset({"comment"})
    assert "comment" not in row.provided_columns
    assert row.input_columns["comment"] == "CLEAR"


def test_mixed_update_and_clear(tmp_path):
    path = tmp_path / "edit_wallet_mixed.xlsx"
    _write_xlsx(
        path,
        [{"card": "9990110810347534", "comment": "CLEAR", "phone": "79491103311"}],
    )
    batch = prepare_edit_wallet_batch(str(path))
    row = batch.rows[0]
    assert row.cleared_columns == frozenset({"comment"})
    assert row.provided_columns == frozenset({"phone"})


def test_clear_only_row_is_runnable(tmp_path):
    path = tmp_path / "edit_wallet_clear_only.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "groups": "clear"}])
    batch = prepare_edit_wallet_batch(str(path))
    assert len(batch.rows) == 1
    assert batch.rows[0].cleared_columns == frozenset({"groups"})


def test_clear_blacklist_status_invalid(tmp_path):
    path = tmp_path / "edit_wallet_bad_clear.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "status": "CLEAR"}])
    batch = prepare_edit_wallet_batch(str(path))
    assert batch.rows == []
    assert batch.invalid_rows[0].result == RESULT_FAIL_INVALID
    assert batch.invalid_rows[0].comment == "CLEAR not allowed for column: status"


@pytest.mark.parametrize("column", ["aggregate", "gender", "balance"])
def test_clear_blacklist_columns_invalid(tmp_path, column):
    path = tmp_path / f"edit_wallet_bad_{column}.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", column: "CLEAR"}])
    batch = prepare_edit_wallet_batch(str(path))
    assert batch.rows == []
    assert f"CLEAR not allowed for column: {column}" in batch.invalid_rows[0].comment


def test_partners_clear_not_parsed_as_partner_name(tmp_path):
    path = tmp_path / "edit_wallet_partners_clear.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "partners": "CLEAR"}])
    batch = prepare_edit_wallet_batch(str(path))
    row = batch.rows[0]
    assert row.cleared_columns == frozenset({"partners"})
    assert row.partners == ""


def test_groups_clear_not_parsed_as_group_name(tmp_path):
    path = tmp_path / "edit_wallet_groups_clear.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "groups": "Clear"}])
    batch = prepare_edit_wallet_batch(str(path))
    row = batch.rows[0]
    assert row.cleared_columns == frozenset({"groups"})
    assert row.groups == ""


def test_build_success_comment_format():
    row = EditWalletRow(
        row_number=2,
        card="9990110810347534",
        provided_columns=frozenset({"phone"}),
        cleared_columns=frozenset({"comment", "groups"}),
    )
    assert (
        build_success_comment(row)
        == "card found after save; cleared=comment,groups; updated=phone"
    )
