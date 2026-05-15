"""CLI tests for ``tools/validate_rules_xlsx.py`` (contract C2+C3 wrapper)."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from core.rules_v2.contract_errors import RULE_UNKNOWN_COLUMN
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from tools.validate_rules_xlsx import main, validate_rules_xlsx

C5_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "rules_v2"
    / "c5"
    / "workbooks"
    / "baseline_prod_synthetic.xlsx"
)


def _empty_workbook_frames() -> dict[str, pd.DataFrame]:
    return {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }


def _write_workbook(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


def _build_minimal_valid_frames() -> dict[str, pd.DataFrame]:
    """Minimal rows so ``build_snapshot_v2_from_legacy`` succeeds."""

    frames = _empty_workbook_frames()
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 2},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "cli_test"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [{"chat_id": "1", "user_id": "1", "level": 1, "enabled": 1, "note": ""}]
    )
    frames["commands"] = pd.DataFrame(
        [
            {
                "command": "/start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
                "note": "",
            }
        ]
    )
    return frames


def _build_unknown_column_workbook(path: Path) -> None:
    frames = _build_minimal_valid_frames()
    frames["wallet_limits"] = pd.DataFrame(
        [
            {
                "id": "LIM-00001",
                "enabled": 0,
                "analyzers": "wallet",
                "scope": "partner",
                "scope_value": "P1",
                "limit_type": "daily_max_amount",
                "limit_value": 1,
                "reason": "",
                "method": "",
                "comment": "",
                "extra_diag": "",
            }
        ]
    )
    _write_workbook(path, frames)


@pytest.fixture
def valid_workbook(tmp_path: Path) -> Path:
    assert C5_BASELINE.is_file(), f"missing fixture: {C5_BASELINE}"
    path = tmp_path / "valid.xlsx"
    path.write_bytes(C5_BASELINE.read_bytes())
    return path


@pytest.fixture
def unknown_column_workbook(tmp_path: Path) -> Path:
    path = tmp_path / "unknown_col.xlsx"
    _build_unknown_column_workbook(path)
    return path


def _run_cli(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["validate_rules_xlsx.py", *argv])
    return code, buf.getvalue()


def test_cli_valid_workbook_exit_zero(valid_workbook: Path) -> None:
    code, out = _run_cli(str(valid_workbook))
    assert code == 0
    assert "publish_allowed=True" in out


def test_cli_strict_unknown_column_exit_nonzero(unknown_column_workbook: Path) -> None:
    code, out = _run_cli(str(unknown_column_workbook), "--strict")
    assert code != 0
    assert RULE_UNKNOWN_COLUMN in out
    assert "field=extra_diag" in out
    assert "publish_allowed=False" in out


def test_cli_legacy_unknown_column_exit_zero_with_warn(unknown_column_workbook: Path) -> None:
    code, out = _run_cli(str(unknown_column_workbook))
    assert code == 0
    assert RULE_UNKNOWN_COLUMN in out
    assert "[WARN]" in out
    assert "publish_allowed=True" in out


def test_validate_rules_xlsx_matches_evaluate_snapshot_publish(
    unknown_column_workbook: Path,
) -> None:
    from core.rules_v2.contract_publish import (
        ContractValidationMode,
        evaluate_snapshot_publish,
    )

    cli_decision = validate_rules_xlsx(unknown_column_workbook, strict=True)
    pub_decision = evaluate_snapshot_publish(
        unknown_column_workbook,
        policy_mode=ContractValidationMode.STRICT,
    )
    assert cli_decision.contract_issues == pub_decision.contract_issues
    assert cli_decision.publish_allowed == pub_decision.publish_allowed


def test_missing_workbook_exit_two(tmp_path: Path) -> None:
    missing = tmp_path / "nope.xlsx"
    code, out = _run_cli(str(missing))
    assert code == 2
    assert "WORKBOOK_NOT_FOUND" in out
