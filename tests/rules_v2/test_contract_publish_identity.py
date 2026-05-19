"""PR-5: C3.5 identity compare wired into ``evaluate_snapshot_publish`` (default off)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from core.rules_v2.contract_errors import RULE_IMMUTABLE_ID_VIOLATION
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    SnapshotPublishDecision,
    evaluate_snapshot_publish,
)
from core.rules_v2.identity_drift import IdentityRow, IdentityRegistry
from core.rules_v2.identity_registry_io import save_identity_registry
from core.rules_v2.validation_issues import ValidationSeverity, has_blocking_errors

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


def _decision_signature(d: SnapshotPublishDecision) -> dict:
    return {
        "publish_allowed": d.publish_allowed,
        "contract_issues": d.contract_issues,
        "blocking_issue_codes": d.blocking_issue_codes,
        "error_count": d.error_count,
        "warning_count": d.warning_count,
        "info_count": d.info_count,
        "has_blocking_contract": d.has_blocking_contract,
        "snapshot_fingerprint": d.snapshot_fingerprint,
        "load_error": d.load_error,
        "build_error": d.build_error,
        "validation_crash": d.validation_crash,
    }


_IDENTITY_DRIFT_FN = "core.rules_v2.contract_publish._identity_drift_issues"


@pytest.fixture(autouse=True)
def _clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RULES_IDENTITY_DISABLED", raising=False)
    monkeypatch.delenv("RULES_IDENTITY_COMPARE", raising=False)


def test_default_env_identity_not_called(rules_xlsx_baseline: Path) -> None:
    with patch(_IDENTITY_DRIFT_FN) as drift_m:
        a = evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.LEGACY)
        b = evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.LEGACY)
    drift_m.assert_not_called()
    assert _decision_signature(a) == _decision_signature(b)


def test_identity_disabled_env_not_called(rules_xlsx_baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_DISABLED", "1")
    with patch(_IDENTITY_DRIFT_FN) as drift_m:
        evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.STRICT)
    drift_m.assert_not_called()


def test_identity_compare_off_not_called(rules_xlsx_baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "off")
    with patch(_IDENTITY_DRIFT_FN) as drift_m:
        evaluate_snapshot_publish(rules_xlsx_baseline, policy_mode=ContractValidationMode.STRICT)
    drift_m.assert_not_called()


def _drift_workbook_and_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from tests.rules_v2.test_identity_manifest import _minimal_frames, _write_workbook

    frames = _minimal_frames()
    frames["thresholds_partner"] = pd.DataFrame(
        [
            {
                "id": "THR-DRIFT",
                "enabled": 1,
                "analyzer": "wallet",
                "partner": "Partner (1)",
                "metric": "count",
                "reason": "r",
            }
        ]
    )
    path = _write_workbook(tmp_path, frames, "drift.xlsx")
    registry_path = tmp_path / "registry.json"
    monkeypatch.setenv("RULES_IDENTITY_REGISTRY_PATH", str(registry_path))
    old_axes = {"job_key": "wallet", "partner_key": "partner_a", "metric_key": "count"}
    save_identity_registry(
        IdentityRegistry(
            rows=(
                IdentityRow(
                    sheet="thresholds_partner",
                    id="THR-DRIFT",
                    immutable_axes=old_axes,
                ),
            ),
            schema_version="rules_identity_registry.v1",
            workbook_path=str(path),
        )
    )
    frames["thresholds_partner"] = pd.DataFrame(
        [
            {
                "id": "THR-DRIFT",
                "enabled": 1,
                "analyzer": "wallet",
                "partner": "Other (2)",
                "metric": "count",
                "reason": "r",
            }
        ]
    )
    return _write_workbook(tmp_path, frames, "drift_changed.xlsx")


def test_enabled_compare_drift_in_contract_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "enforce")
    path = _drift_workbook_and_registry(tmp_path, monkeypatch)
    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)
    drift = [i for i in decision.contract_issues if i.code == RULE_IMMUTABLE_ID_VIOLATION]
    assert len(drift) >= 1


def test_strict_enabled_drift_blocks_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "on")
    path = _drift_workbook_and_registry(tmp_path, monkeypatch)
    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)
    assert decision.publish_allowed is False
    assert RULE_IMMUTABLE_ID_VIOLATION in decision.blocking_issue_codes
    assert has_blocking_errors(decision.contract_issues)


def test_legacy_enabled_drift_warn_publish_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "enforce")
    path = _drift_workbook_and_registry(tmp_path, monkeypatch)
    decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.LEGACY)
    assert decision.publish_allowed is True
    drift = [i for i in decision.contract_issues if i.code == RULE_IMMUTABLE_ID_VIOLATION]
    assert drift
    assert drift[0].severity == ValidationSeverity.WARN
    assert not has_blocking_errors(decision.contract_issues)


def test_load_error_skips_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "on")
    with (
        patch("core.rules_v2.contract_publish.read_workbook_headers", side_effect=OSError("boom")),
        patch(_IDENTITY_DRIFT_FN) as drift_m,
    ):
        decision = evaluate_snapshot_publish("/nonexistent/rules.xlsx", policy_mode=ContractValidationMode.STRICT)
    assert decision.load_error is not None
    drift_m.assert_not_called()


def test_build_error_skips_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULES_IDENTITY_COMPARE", "on")
    path = tmp_path / "ok.xlsx"
    from tests.rules_v2.test_identity_manifest import _minimal_frames, _write_workbook

    _write_workbook(tmp_path, _minimal_frames(), "ok.xlsx")
    with (
        patch(
            "core.rules_v2.contract_publish.build_snapshot_v2_from_legacy",
            side_effect=ValueError("build failed"),
        ),
        patch(_IDENTITY_DRIFT_FN) as drift_m,
    ):
        decision = evaluate_snapshot_publish(path, policy_mode=ContractValidationMode.STRICT)
    assert decision.build_error is not None
    drift_m.assert_not_called()


@pytest.fixture
def rules_xlsx_baseline(monkeypatch: pytest.MonkeyPatch) -> Path:
    assert C5_BASELINE.is_file()
    monkeypatch.setenv("RULES_XLSX_PATH", str(C5_BASELINE))
    return C5_BASELINE
