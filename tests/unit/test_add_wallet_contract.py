from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from automation.add_wallet_contract import (
    DEFAULT_DIRECTION,
    DEFAULT_POOL,
    DEFAULT_STATE,
    DEFAULT_STATUS,
    ExcelRouting,
    RESULT_DRY_RUN,
    RESULT_FAIL_INVALID,
    RESULT_OK,
    RESULT_SKIP_DUP_FILE,
    AddWalletBatchSummary,
    detect_excel_routing,
    prepare_add_wallet_batch,
)
from automation.audit import normalize_card_digits as audit_normalize


def _write_xlsx(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    df = pd.DataFrame(rows, columns=columns)
    df.to_excel(path, index=False)


def test_accepts_minimal_file(tmp_path):
    path = tmp_path / "add.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "phone": "79491103311"}])
    batch = prepare_add_wallet_batch(str(path))
    assert len(batch.rows) == 1
    row = batch.rows[0]
    assert row.card == "9990110810347534"
    assert row.phone == "79491103311"
    assert row.status == DEFAULT_STATUS
    assert row.state == DEFAULT_STATE
    assert row.direction == DEFAULT_DIRECTION
    assert row.pool == DEFAULT_POOL


def test_rejects_missing_card(tmp_path):
    path = tmp_path / "bad.xlsx"
    _write_xlsx(path, [{"phone": "79491103311"}])
    with pytest.raises(ValueError, match="card"):
        prepare_add_wallet_batch(str(path))


def test_rejects_missing_phone(tmp_path):
    path = tmp_path / "bad.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534"}])
    with pytest.raises(ValueError, match="phone"):
        prepare_add_wallet_batch(str(path))


def test_rejects_disable_contract(tmp_path):
    path = tmp_path / "disable.xlsx"
    _write_xlsx(
        path,
        [{"card": "4111111111111111", "action": "remove_partner", "value": "P1"}],
    )
    with pytest.raises(ValueError, match="action/value"):
        prepare_add_wallet_batch(str(path))


def test_detects_duplicate_card_inside_file(tmp_path):
    path = tmp_path / "dup.xlsx"
    _write_xlsx(
        path,
        [
            {"card": "9990110810347534", "phone": "79491103311"},
            {"card": "9990110810347534", "phone": "79491103312"},
        ],
    )
    batch = prepare_add_wallet_batch(str(path))
    assert len(batch.rows) == 1
    assert len(batch.invalid_rows) == 1
    assert batch.invalid_rows[0].result == RESULT_SKIP_DUP_FILE


def test_normalizes_card_digits(tmp_path):
    path = tmp_path / "norm.xlsx"
    _write_xlsx(path, [{"card": "9990 1108 1034 7534.0", "phone": "79491103311"}])
    batch = prepare_add_wallet_batch(str(path))
    assert batch.rows[0].card == "9990110810347534"
    assert audit_normalize("9990 1108 1034 7534.0") == "9990110810347534"


def test_applies_default_values(tmp_path):
    path = tmp_path / "defaults.xlsx"
    _write_xlsx(path, [{"card": "9990110810347534", "phone": "79491103311"}])
    row = prepare_add_wallet_batch(str(path)).rows[0]
    assert row.status == DEFAULT_STATUS
    assert row.state == DEFAULT_STATE
    assert row.direction == DEFAULT_DIRECTION
    assert row.pool == DEFAULT_POOL


def test_invalid_row_empty_card(tmp_path):
    path = tmp_path / "empty_card.xlsx"
    _write_xlsx(path, [{"card": "", "phone": "79491103311"}])
    batch = prepare_add_wallet_batch(str(path))
    assert batch.rows == []
    assert batch.invalid_rows[0].result == RESULT_FAIL_INVALID


def test_routing_disable_vs_add_wallet(tmp_path):
    disable_path = tmp_path / "disable.xlsx"
    _write_xlsx(
        disable_path,
        [{"card": "4111", "action": "remove_partner", "value": "x"}],
    )
    add_path = tmp_path / "add.xlsx"
    _write_xlsx(add_path, [{"card": "4111", "phone": "79491103311"}])

    assert detect_excel_routing(str(disable_path))[0] == ExcelRouting.DISABLE
    assert detect_excel_routing(str(add_path))[0] == ExcelRouting.ADD_WALLET


def test_routing_add_wallet_filename_prefix(tmp_path):
    path = tmp_path / "add_wallet_batch.xlsx"
    _write_xlsx(path, [{"card": "4111", "phone": "79491103311"}])
    routing, err = detect_excel_routing(str(path), original_filename="add_wallet_batch.xlsx")
    assert routing == ExcelRouting.ADD_WALLET
    assert err is None


def test_batch_summary_counts():
    summary = AddWalletBatchSummary(dry_run=False)
    summary.record(RESULT_OK)
    summary.record(RESULT_DRY_RUN)
    summary.record(RESULT_SKIP_DUP_FILE)
    summary.record(RESULT_FAIL_INVALID)
    assert summary.total == 4
    assert summary.ok == 1
    assert summary.dry_run_would_create == 1
    assert summary.skip == 1
    assert summary.fail == 1
    assert "[WalletEditorAdd]" in summary.telegram_summary()
