"""Workbook ``meta.version`` validation (CONTRACT_V2 §2.1 / §19 / §18)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.rules_v2.contract_errors import RULE_INVALID_META_VERSION
from core.rules_v2.contract_publish import ContractValidationMode, evaluate_snapshot_publish
from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.validation_issues import ValidationSeverity, has_blocking_errors
from core.rules_v2.validation_workbook import (
    SUPPORTED_META_VERSIONS,
    parse_contract_meta_version,
    validate_meta_version,
)


def _empty_frames() -> dict[str, pd.DataFrame]:
    return {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }


def _write_workbook(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


def _minimal_workbook(
    tmp_path: Path,
    *,
    meta_rows: list[dict[str, object]] | None,
    name: str = "rules_meta.xlsx",
) -> Path:
    frames = _empty_frames()
    if meta_rows is not None:
        frames["meta"] = pd.DataFrame(meta_rows)
    path = tmp_path / name
    _write_workbook(path, frames)
    return path


def test_parse_contract_meta_version_accepts_integers():
    assert parse_contract_meta_version(3) == 3
    assert parse_contract_meta_version(3.0) == 3
    assert parse_contract_meta_version("3") == 3
    assert parse_contract_meta_version(None) is None
    assert parse_contract_meta_version("legacy") is None
    assert parse_contract_meta_version("v3") is None
    assert parse_contract_meta_version(3.5) is None


def test_valid_meta_version_no_issue(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
        ],
    )

    issues = validate_meta_version(path, strict=True)

    assert issues == []


def test_missing_meta_version_key(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[{"key": "updated_at", "value": "15.05.2026 00:00:00"}],
    )

    strict = validate_meta_version(path, strict=True)
    legacy = validate_meta_version(path, strict=False)

    assert len(strict) == 1
    assert strict[0].code == RULE_INVALID_META_VERSION
    assert strict[0].severity == ValidationSeverity.ERROR
    assert legacy[0].severity == ValidationSeverity.WARN
    assert has_blocking_errors(strict)
    assert not has_blocking_errors(legacy)


def test_non_integer_meta_version(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[{"key": "version", "value": "legacy"}],
    )

    issues = validate_meta_version(path, strict=True)

    assert len(issues) == 1
    assert issues[0].code == RULE_INVALID_META_VERSION
    assert issues[0].details["reason"] == "non_integer"


def test_unsupported_meta_version_integer(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[{"key": "version", "value": 99}],
    )

    issues = validate_meta_version(path, strict=True)

    assert len(issues) == 1
    assert issues[0].details["reason"] == "unsupported"
    assert issues[0].details["parsed"] == 99
    assert 99 not in SUPPORTED_META_VERSIONS


def test_strict_publish_blocked_unsupported_version(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[
            {"key": "version", "value": 2},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
        ],
    )

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)

    assert decision.publish_allowed is False
    assert RULE_INVALID_META_VERSION in decision.blocking_issue_codes


def test_legacy_publish_allowed_unsupported_version_warns(tmp_path: Path) -> None:
    path = _minimal_workbook(
        tmp_path,
        meta_rows=[{"key": "version", "value": 2}],
    )

    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)

    assert decision.publish_allowed is True
    meta_issues = [i for i in decision.contract_issues if i.code == RULE_INVALID_META_VERSION]
    assert len(meta_issues) == 1
    assert meta_issues[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)
