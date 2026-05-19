"""PR-2: ``extract_identity_manifest`` — deterministic Excel-row identity manifest."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from core.rules_v2.contract_schema import SHEET_SCHEMAS
from core.rules_v2.identity_axes import IDENTITY_SHEET_ORDER
from core.rules_v2.identity_drift import (
    canonical_manifest_json,
    extract_identity_manifest,
    manifest_row_key,
    manifest_sha256,
)

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"

CORE_ROOT = Path(__file__).resolve().parents[2] / "core"


def _minimal_frames() -> dict[str, pd.DataFrame]:
    frames = {
        name: pd.DataFrame(columns=sorted(schema.required_columns | schema.optional_columns))
        for name, schema in SHEET_SCHEMAS.items()
    }
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 3},
            {"key": "updated_at", "value": "15.05.2026 00:00:00"},
            {"key": "updated_by", "value": "test"},
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


def _write_workbook(tmp_path: Path, frames: dict[str, pd.DataFrame], name: str = "rules.xlsx") -> Path:
    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _seven_sheet_identity_frames() -> dict[str, pd.DataFrame]:
    frames = _minimal_frames()
    frames["wallet_limits"] = pd.DataFrame(
        [
            {
                "id": "LIM-001",
                "enabled": 1,
                "analyzers": "wallet",
                "scope": "partner",
                "scope_value": "Partner (1)",
                "limit_type": "daily_max_amount",
                "limit_value": 100,
                "reason": "r",
            }
        ]
    )
    frames["thresholds_partner"] = pd.DataFrame(
        [
            {
                "id": "THR-001",
                "enabled": 1,
                "analyzer": "wallet",
                "partner": "Partner (1)",
                "metric": "count",
                "reason": "r",
            }
        ]
    )
    frames["job_params"] = pd.DataFrame(
        [
            {
                "id": "JP-001",
                "enabled": 1,
                "job": "wallet",
                "scope": "global",
                "scope_value": "*",
                "key": "window_minutes",
                "value_type": "int",
                "value": 60,
            }
        ]
    )
    frames["schedules"] = pd.DataFrame(
        [
            {
                "id": "SCH-001",
                "enabled": 1,
                "job_type": "wallet",
                "schedule_type": "interval",
                "every_seconds": 300,
                "cron": "",
                "jitter_sec": 0,
                "max_runtime_sec": 600,
                "coalesce": 1,
            }
        ]
    )
    frames["exclude_time"] = pd.DataFrame(
        [
            {
                "id": "EX-001",
                "enabled": 1,
                "analyzers": "wallet",
                "partner": "Partner (1)",
                "start_dt": "01.01.2026 00:00:00",
                "end_dt": "02.01.2026 00:00:00",
                "reason": "r",
            }
        ]
    )
    frames["partner_groups"] = pd.DataFrame(
        [
            {
                "id": "PG-001",
                "enabled": 1,
                "analyzers": "wallet",
                "group_name": "G1",
                "partner": "Partner (1)",
            }
        ]
    )
    frames["ui_layout"] = pd.DataFrame(
        [
            {
                "id": "UI-001",
                "enabled": 1,
                "view": "hourly",
                "section": "payins",
                "order": 1,
                "key": "payins.title",
            }
        ]
    )
    return frames


def test_extract_twice_identical_canonical_hash(tmp_path: Path) -> None:
    path = _write_workbook(tmp_path, _seven_sheet_identity_frames())
    a = extract_identity_manifest(path)
    b = extract_identity_manifest(path)
    assert manifest_sha256(a) == manifest_sha256(b)
    assert canonical_manifest_json(a) == canonical_manifest_json(b)


def test_wallet_limits_one_manifest_row_per_excel_row(tmp_path: Path) -> None:
    frames = _minimal_frames()
    frames["wallet_limits"] = pd.DataFrame(
        [
            {
                "id": "LIM-MULTI",
                "enabled": 1,
                "analyzers": "wallet,hourly",
                "scope": "global",
                "scope_value": "*",
                "limit_type": "daily_max_amount",
                "limit_value": 50,
                "reason": "r",
            }
        ]
    )
    path = _write_workbook(tmp_path, frames)
    manifest = extract_identity_manifest(path)
    lim_rows = [r for r in manifest.rows if r.sheet == "wallet_limits"]
    assert len(lim_rows) == 1
    assert lim_rows[0].id == "LIM-MULTI"
    assert lim_rows[0].immutable_axes["job_keys"] == ["hourly", "wallet"]


def test_empty_id_row_skipped(tmp_path: Path) -> None:
    frames = _minimal_frames()
    frames["wallet_limits"] = pd.DataFrame(
        [
            {
                "id": "",
                "enabled": 1,
                "analyzers": "wallet",
                "scope": "global",
                "scope_value": "*",
                "limit_type": "daily_max_amount",
                "limit_value": 1,
                "reason": "r",
            },
            {
                "id": "LIM-OK",
                "enabled": 1,
                "analyzers": "wallet",
                "scope": "global",
                "scope_value": "*",
                "limit_type": "daily_max_amount",
                "limit_value": 2,
                "reason": "r",
            },
        ]
    )
    path = _write_workbook(tmp_path, frames)
    manifest = extract_identity_manifest(path)
    keys = {r.key for r in manifest.rows if r.sheet == "wallet_limits"}
    assert keys == {manifest_row_key("wallet_limits", "LIM-OK")}


def test_all_seven_sheets_represented(tmp_path: Path) -> None:
    path = _write_workbook(tmp_path, _seven_sheet_identity_frames())
    manifest = extract_identity_manifest(path)
    sheets = {r.sheet for r in manifest.rows}
    assert sheets == set(IDENTITY_SHEET_ORDER)


def test_mutable_field_change_does_not_change_immutable_axes(tmp_path: Path) -> None:
    frames = _minimal_frames()
    base = {
        "id": "LIM-MUT",
        "enabled": 1,
        "analyzers": "wallet",
        "scope": "partner",
        "scope_value": "Partner (1)",
        "limit_type": "daily_max_amount",
        "limit_value": 100,
        "reason": "r",
    }
    frames["wallet_limits"] = pd.DataFrame([base])
    path_a = _write_workbook(tmp_path, frames, "a.xlsx")
    changed = dict(base)
    changed["limit_value"] = 999
    changed["reason"] = "other"
    frames["wallet_limits"] = pd.DataFrame([changed])
    path_b = _write_workbook(tmp_path, frames, "b.xlsx")
    row_a = extract_identity_manifest(path_a).rows[0]
    row_b = extract_identity_manifest(path_b).rows[0]
    assert row_a.immutable_axes == row_b.immutable_axes


def test_immutable_field_change_changes_axes(tmp_path: Path) -> None:
    frames = _minimal_frames()
    base = {
        "id": "THR-IMM",
        "enabled": 1,
        "analyzer": "wallet",
        "partner": "Partner (1)",
        "metric": "count",
        "reason": "r",
    }
    frames["thresholds_partner"] = pd.DataFrame([base])
    path_a = _write_workbook(tmp_path, frames, "a.xlsx")
    changed = dict(base)
    changed["partner"] = "Other (2)"
    frames["thresholds_partner"] = pd.DataFrame([changed])
    path_b = _write_workbook(tmp_path, frames, "b.xlsx")
    row_a = extract_identity_manifest(path_a).rows[0]
    row_b = extract_identity_manifest(path_b).rows[0]
    assert row_a.immutable_axes != row_b.immutable_axes


def test_baseline_prod_synthetic_deterministic_when_rows_present() -> None:
    if not C5_BASELINE.is_file():
        pytest.skip("C5 baseline workbook missing")
    a = extract_identity_manifest(C5_BASELINE)
    b = extract_identity_manifest(C5_BASELINE)
    assert manifest_sha256(a) == manifest_sha256(b)


def test_no_production_runtime_imports_identity_drift() -> None:
    allowed = {
        (CORE_ROOT / "rules_v2" / "identity_drift.py").resolve(),
        (CORE_ROOT / "rules_v2" / "identity_axes.py").resolve(),
        (CORE_ROOT / "rules_v2" / "identity_registry_io.py").resolve(),
        (CORE_ROOT / "rules_v2" / "contract_publish.py").resolve(),
    }
    forbidden_modules = ("identity_drift", "identity_axes", "identity_registry_io")
    for py in CORE_ROOT.rglob("*.py"):
        if py.resolve() in allowed:
            continue
        text = py.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for frag in forbidden_modules:
                        assert frag not in alias.name, f"{py}: import {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for frag in forbidden_modules:
                        assert frag not in node.module, f"{py}: from {node.module}"


def test_identity_sheet_order_matches_contract() -> None:
    assert IDENTITY_SHEET_ORDER == (
        "wallet_limits",
        "thresholds_partner",
        "job_params",
        "schedules",
        "exclude_time",
        "partner_groups",
        "ui_layout",
    )
