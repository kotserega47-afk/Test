"""Tests for ``core.rules_writer`` pre-upload validation (contract STRICT gate)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.rules_v2.contract_errors import RULE_DUPLICATE_LIMIT, RULE_EMPTY_JOBS, make_issue
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    SnapshotPublishDecision,
)
from core.rules_v2.validation_issues import ValidationSeverity
from core.rules_writer import _validate_rules_file


def _decision(
    *,
    publish_allowed: bool = True,
    contract_issues: tuple = (),
    load_error: str | None = None,
    build_error: str | None = None,
    validation_crash: str | None = None,
) -> SnapshotPublishDecision:
    return SnapshotPublishDecision(
        workbook_path="/tmp/rules.xlsx",
        policy_mode="strict",
        validators_strict=True,
        contract_issues=contract_issues,
        has_blocking_contract=not publish_allowed and not load_error,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint=None,
        snapshot=None,
        publish_allowed=publish_allowed,
        load_error=load_error,
        build_error=build_error,
        validation_crash=validation_crash,
    )


def test_validate_rules_file_passes_when_publish_allowed() -> None:
    decision = _decision(publish_allowed=True)

    with patch(
        "core.rules_writer.evaluate_snapshot_publish",
        return_value=decision,
    ) as mock_eval:
        _validate_rules_file(Path("/tmp/rules.xlsx"))

    mock_eval.assert_called_once_with(
        Path("/tmp/rules.xlsx"),
        policy_mode=ContractValidationMode.STRICT,
    )


def test_validate_rules_file_raises_on_blocking_issue() -> None:
    issue = make_issue(
        RULE_DUPLICATE_LIMIT,
        "duplicate limit key",
        sheet="wallet_limits",
        severity=ValidationSeverity.ERROR,
    )
    decision = _decision(
        publish_allowed=False,
        contract_issues=(issue,),
    )

    with patch("core.rules_writer.evaluate_snapshot_publish", return_value=decision):
        with pytest.raises(RuntimeError) as exc_info:
            _validate_rules_file(Path("/tmp/rules.xlsx"))

    msg = str(exc_info.value)
    assert "rules validation failed" in msg
    assert RULE_DUPLICATE_LIMIT in msg
    assert "wallet_limits:" in msg


def test_validate_rules_file_raises_on_load_error() -> None:
    decision = _decision(publish_allowed=False, load_error="OSError: cannot read")

    with patch("core.rules_writer.evaluate_snapshot_publish", return_value=decision):
        with pytest.raises(RuntimeError) as exc_info:
            _validate_rules_file(Path("/tmp/rules.xlsx"))

    assert "workbook: load:" in str(exc_info.value)


def test_validate_rules_file_raises_on_build_error() -> None:
    decision = _decision(publish_allowed=False, build_error="ValueError: bad row")

    with patch("core.rules_writer.evaluate_snapshot_publish", return_value=decision):
        with pytest.raises(RuntimeError) as exc_info:
            _validate_rules_file(Path("/tmp/rules.xlsx"))

    assert "workbook: build:" in str(exc_info.value)


def test_validate_rules_file_warn_only_does_not_raise() -> None:
    issue = make_issue(
        RULE_DUPLICATE_LIMIT,
        "legacy-style warn",
        sheet="wallet_limits",
        severity=ValidationSeverity.WARN,
    )
    decision = _decision(publish_allowed=True, contract_issues=(issue,))

    with patch("core.rules_writer.evaluate_snapshot_publish", return_value=decision):
        _validate_rules_file(Path("/tmp/rules.xlsx"))


def test_rules_writer_does_not_import_legacy_validators() -> None:
    import core.rules_writer as rw

    source = Path(rw.__file__).read_text(encoding="utf-8")
    assert "core.rules_v2.validators" not in source
    assert "build_snapshot_v2_from_legacy" not in source
    assert "check_rules_xlsx" not in source


def test_validate_rules_file_does_not_call_legacy_validators_or_bridge() -> None:
    decision = _decision(publish_allowed=True)

    with (
        patch("core.rules_writer.evaluate_snapshot_publish", return_value=decision),
        patch("core.rules_v2.validators.validate_snapshot") as mock_legacy,
        patch("core.rules_v2.bridge_legacy.build_snapshot_v2_from_legacy") as mock_build,
    ):
        _validate_rules_file(Path("/tmp/rules.xlsx"))

    mock_legacy.assert_not_called()
    mock_build.assert_not_called()


def test_validate_rules_file_empty_jobs_blocks_under_strict(tmp_path: Path) -> None:
    from tests.rules_v2.test_contract_publish_c4 import _workbook_empty_jobs

    path = _workbook_empty_jobs(tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        _validate_rules_file(path)

    assert RULE_EMPTY_JOBS in str(exc_info.value)
