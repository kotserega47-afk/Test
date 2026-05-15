"""Workbook commands policy validation (RULE_DUPLICATE_COMMAND_POLICY)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.rules_v2.contract_errors import RULE_DUPLICATE_COMMAND_POLICY
from core.rules_v2.contract_publish import ContractValidationMode, evaluate_snapshot_publish
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.validation_issues import ValidationSeverity, has_blocking_errors
from core.rules_v2.validation_commands import validate_duplicate_command_policies


def _empty_frames() -> dict[str, pd.DataFrame]:
    return {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }


def _write_workbook(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


def _workbook_with_commands(
    tmp_path: Path,
    command_rows: list[dict[str, object]],
    *,
    meta_version: int = 3,
) -> Path:
    frames = _empty_frames()
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": meta_version},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
        ]
    )
    frames["commands"] = pd.DataFrame(command_rows)
    path = tmp_path / "commands_policy.xlsx"
    _write_workbook(path, frames)
    return path


def test_conflicting_policies_same_command_key_different_levels(tmp_path: Path) -> None:
    path = _workbook_with_commands(
        tmp_path,
        [
            {
                "command": "start",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
            {
                "command": "/START",
                "required_level": 2,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
        ],
    )

    strict = validate_duplicate_command_policies(path, strict=True)
    legacy = validate_duplicate_command_policies(path, strict=False)

    assert len(strict) == 1
    assert strict[0].code == RULE_DUPLICATE_COMMAND_POLICY
    assert strict[0].sheet == "commands"
    assert strict[0].field == "command"
    assert strict[0].severity == ValidationSeverity.ERROR
    assert "command_key" in strict[0].message or "start" in strict[0].message.lower()

    assert len(legacy) == 1
    assert legacy[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(legacy)


def test_same_command_key_and_policy_no_issue(tmp_path: Path) -> None:
    path = _workbook_with_commands(
        tmp_path,
        [
            {
                "command": "ping",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 0,
                "enabled": 1,
            },
            {
                "command": "/ping",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 0,
                "enabled": 1,
            },
        ],
    )

    issues = validate_duplicate_command_policies(path, strict=True)

    assert issues == []


def test_disabled_conflicting_row_ignored(tmp_path: Path) -> None:
    path = _workbook_with_commands(
        tmp_path,
        [
            {
                "command": "help",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
            {
                "command": "HELP",
                "required_level": 9,
                "allow_private": 0,
                "allow_groups": 0,
                "enabled": 0,
            },
        ],
    )

    issues = validate_duplicate_command_policies(path, strict=True)

    assert issues == []


def test_strict_publish_blocked(tmp_path: Path) -> None:
    path = _workbook_with_commands(
        tmp_path,
        [
            {
                "command": "go",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
            {
                "command": "GO",
                "required_level": 3,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
        ],
    )

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.publish_allowed is False
    assert RULE_DUPLICATE_COMMAND_POLICY in decision.blocking_issue_codes


def test_legacy_publish_allowed_with_warn(tmp_path: Path) -> None:
    path = _workbook_with_commands(
        tmp_path,
        [
            {
                "command": "go",
                "required_level": 1,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
            {
                "command": "GO",
                "required_level": 3,
                "allow_private": 1,
                "allow_groups": 1,
                "enabled": 1,
            },
        ],
    )

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    policy_issues = [i for i in decision.contract_issues if i.code == RULE_DUPLICATE_COMMAND_POLICY]
    assert len(policy_issues) == 1
    assert policy_issues[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)
