"""PR-3: ``compare_identity_registry`` — pure drift compare (unwired)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.rules_v2.contract_errors import RULE_IMMUTABLE_ID_VIOLATION
from core.rules_v2.identity_drift import (
    IdentityManifest,
    IdentityRegistry,
    IdentityRow,
    compare_identity_registry,
)
from core.rules_v2.validation_issues import ValidationSeverity, is_blocking

CORE_ROOT = Path(__file__).resolve().parents[2] / "core"


def _row(
    sheet: str,
    row_id: str,
    axes: dict,
) -> IdentityRow:
    return IdentityRow(sheet=sheet, id=row_id, immutable_axes=axes)


def _manifest(*rows: IdentityRow) -> IdentityManifest:
    ordered = tuple(sorted(rows, key=lambda r: (r.sheet, r.id)))
    return IdentityManifest(workbook_path="/tmp/t.xlsx", rows=ordered)


def _registry(*rows: IdentityRow) -> IdentityRegistry:
    ordered = tuple(sorted(rows, key=lambda r: (r.sheet, r.id)))
    return IdentityRegistry(rows=ordered)


def test_bootstrap_registry_none_no_issues() -> None:
    manifest = _manifest(
        _row("wallet_limits", "LIM-1", {"job_keys": ["wallet"], "scope_type": "global", "scope_key": "*", "metric_key": "x", "method_key": None}),
    )
    assert compare_identity_registry(manifest, None, strict=True) == ()


def test_empty_registry_no_issues() -> None:
    manifest = _manifest(
        _row("wallet_limits", "LIM-1", {"job_keys": ["wallet"], "scope_type": "global", "scope_key": "*", "metric_key": "x", "method_key": None}),
    )
    assert compare_identity_registry(manifest, IdentityRegistry(), strict=True) == ()


def test_axis_drift_strict_error_blocking() -> None:
    axes_old = {
        "job_key": "wallet",
        "partner_key": "partner_a",
        "metric_key": "count",
    }
    axes_new = dict(axes_old)
    axes_new["partner_key"] = "partner_b"
    manifest = _manifest(_row("thresholds_partner", "THR-1", axes_new))
    registry = _registry(_row("thresholds_partner", "THR-1", axes_old))
    issues = compare_identity_registry(manifest, registry, strict=True)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == RULE_IMMUTABLE_ID_VIOLATION
    assert issue.severity == ValidationSeverity.ERROR
    assert issue.sheet == "thresholds_partner"
    assert issue.rule_id == "THR-1"
    assert issue.field == "partner_key"
    assert is_blocking(issue)


def test_axis_drift_legacy_warn() -> None:
    axes_old = {"job_key": "wallet", "schedule_type": "interval"}
    axes_new = {"job_key": "hourly", "schedule_type": "interval"}
    manifest = _manifest(_row("schedules", "SCH-1", axes_new))
    registry = _registry(_row("schedules", "SCH-1", axes_old))
    issues = compare_identity_registry(manifest, registry, strict=False)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.WARN
    assert not is_blocking(issues[0])


def test_same_axes_no_issue() -> None:
    axes = {
        "job_keys": ["wallet"],
        "scope_type": "global",
        "scope_key": "*",
        "metric_key": "daily_max_amount",
        "method_key": None,
    }
    row = _row("wallet_limits", "LIM-1", axes)
    manifest = _manifest(row)
    registry = _registry(row)
    assert compare_identity_registry(manifest, registry, strict=True) == ()


def test_new_id_no_issue() -> None:
    axes = {"job_key": "wallet", "partner_key": "p", "metric_key": "m"}
    manifest = _manifest(
        _row("thresholds_partner", "THR-NEW", axes),
    )
    registry = _registry(_row("thresholds_partner", "THR-OLD", axes))
    assert compare_identity_registry(manifest, registry, strict=True) == ()


def test_removed_id_no_issue() -> None:
    axes = {"job_key": "wallet", "partner_key": "p", "metric_key": "m"}
    manifest = _manifest()
    registry = _registry(_row("thresholds_partner", "THR-GONE", axes))
    assert compare_identity_registry(manifest, registry, strict=True) == ()


def test_enabled_not_in_axes_no_issue_on_registry_row_copy() -> None:
    """``enabled`` is mutable and not part of immutable_axes — no drift issue."""
    axes = {"job_key": "wallet", "schedule_type": "cron"}
    manifest = _manifest(_row("schedules", "S-1", axes))
    registry = _registry(_row("schedules", "S-1", axes))
    assert compare_identity_registry(manifest, registry, strict=True) == ()


def test_issue_ordering_stable() -> None:
    axes_a = {"job_key": "a", "partner_key": "p", "metric_key": "m"}
    axes_b = {"job_keys": ["wallet"], "scope_type": "g", "scope_key": "*", "metric_key": "x", "method_key": None}
    manifest = _manifest(
        _row("thresholds_partner", "Z-1", {**axes_a, "partner_key": "z"}),
        _row("wallet_limits", "A-1", {**axes_b, "metric_key": "changed"}),
    )
    registry = _registry(
        _row("thresholds_partner", "Z-1", axes_a),
        _row("wallet_limits", "A-1", axes_b),
    )
    first = compare_identity_registry(manifest, registry, strict=True)
    second = compare_identity_registry(manifest, registry, strict=True)
    assert first == second
    assert [i.sheet for i in first] == ["thresholds_partner", "wallet_limits"]
    assert [i.rule_id for i in first] == ["Z-1", "A-1"]


def test_repeated_compare_identical_tuple() -> None:
    axes_old = {"job_key": "wallet", "partner_key": "p", "metric_key": "m"}
    axes_new = {**axes_old, "metric_key": "other"}
    manifest = _manifest(_row("thresholds_partner", "T-1", axes_new))
    registry = _registry(_row("thresholds_partner", "T-1", axes_old))
    a = compare_identity_registry(manifest, registry, strict=True)
    b = compare_identity_registry(manifest, registry, strict=True)
    assert a == b
    assert len(a) == 1


def test_no_production_runtime_imports_compare() -> None:
    allowed = {
        (CORE_ROOT / "rules_v2" / "identity_drift.py").resolve(),
        (CORE_ROOT / "rules_v2" / "identity_axes.py").resolve(),
        (CORE_ROOT / "rules_v2" / "contract_publish.py").resolve(),
    }
    for py in CORE_ROOT.rglob("*.py"):
        if py.resolve() in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core.rules_v2.identity_drift":
                for alias in node.names:
                    if alias.name == "compare_identity_registry":
                        pytest.fail(f"{py} imports compare_identity_registry")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "core.rules_v2.identity_drift.compare_identity_registry":
                        pytest.fail(f"{py} imports compare_identity_registry")
