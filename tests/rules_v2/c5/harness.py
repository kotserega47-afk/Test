"""C5 regression harness: public validation entrypoints only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from core.rules_v2.validation_issues import ValidationIssue
from core.rules_v2.validation_snapshot import validate_snapshot
from core.rules_v2.validation_workbook import read_workbook_headers, validate_workbook_schema

StageName = Literal["workbook_schema", "snapshot"]


def run_workbook_schema_stage(
    workbook_path: Path | str,
    *,
    strict: bool,
) -> list[ValidationIssue]:
    path = Path(workbook_path)
    headers = read_workbook_headers(path)
    return validate_workbook_schema(headers, strict=strict)


def run_snapshot_stage(
    workbook_path: Path | str,
    *,
    strict: bool,
) -> list[ValidationIssue]:
    path = Path(workbook_path)
    snapshot = build_snapshot_v2_from_legacy(path)
    return validate_snapshot(snapshot, strict=strict)


def run_stages(
    workbook_path: Path | str,
    stages: list[StageName],
    *,
    strict: bool,
) -> dict[StageName, list[ValidationIssue]]:
    out: dict[StageName, list[ValidationIssue]] = {}
    path = Path(workbook_path)
    if "workbook_schema" in stages:
        out["workbook_schema"] = run_workbook_schema_stage(path, strict=strict)
    if "snapshot" in stages:
        out["snapshot"] = run_snapshot_stage(path, strict=strict)
    return out


def load_manifest(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
