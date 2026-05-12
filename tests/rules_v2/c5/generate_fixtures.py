#!/usr/bin/env python3
"""Regenerate C5 workbook fixtures, golden JSON, and baseline freeze metadata.

Run from repo root::

    python tests/rules_v2/c5/generate_fixtures.py

Requires: pandas, openpyxl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.rules_v2.contract_schema import SHEET_SCHEMAS  # noqa: E402
from tests.rules_v2.c5.harness import run_stages  # noqa: E402
from tests.rules_v2.c5.issue_json import dumps_issues_normalized  # noqa: E402
from tests.rules_v2.c5.snapshot_fingerprint import (  # noqa: E402
    rules_snapshot_fingerprint,
    workbook_sha256,
)
from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy  # noqa: E402

C5_DIR = Path(__file__).resolve().parent
WORKBOOKS = C5_DIR / "workbooks"
GOLDEN = C5_DIR / "golden"


def _all_sheet_columns() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, schema in SHEET_SCHEMAS.items():
        cols = sorted(schema.required_columns | schema.optional_columns)
        out[name] = cols
    return out


def _empty_workbook_frames() -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame(columns=cols) for name, cols in _all_sheet_columns().items()}


def _write_workbook(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


def build_baseline_collision_120() -> dict[str, pd.DataFrame]:
    frames = _empty_workbook_frames()
    frames["meta"] = pd.DataFrame(
        [
            {"key": "version", "value": 2},
            {"key": "updated_at", "value": "12.05.2026 00:00:00"},
            {"key": "updated_by", "value": "c5_fixture"},
        ]
    )
    frames["access"] = pd.DataFrame(
        [
            {
                "chat_id": "1",
                "user_id": "1",
                "level": 1,
                "enabled": 1,
                "note": "",
            }
        ]
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
    frames["partner_groups"] = pd.DataFrame(
        [
            {
                "id": "pg1",
                "enabled": 1,
                "analyzers": "wallet",
                "group_name": "Group One",
                "partner": "Partner Alpha (120)",
                "default_method": "",
                "updated_at": "",
                "comment": "",
                "group_priority": "",
                "is_primary": "",
            },
            {
                "id": "pg2",
                "enabled": 1,
                "analyzers": "wallet",
                "group_name": "Group Two",
                "partner": "Partner Beta (120)",
                "default_method": "",
                "updated_at": "",
                "comment": "",
                "group_priority": "",
                "is_primary": "",
            },
        ]
    )
    return frames


def build_baseline_row_swap() -> dict[str, pd.DataFrame]:
    frames = build_baseline_collision_120()
    df = frames["partner_groups"]
    frames["partner_groups"] = df.iloc[[1, 0]].reset_index(drop=True)
    return frames


def build_c2_missing_meta_value() -> dict[str, pd.DataFrame]:
    frames = _empty_workbook_frames()
    # Deliberately omit contract-required ``value`` column on ``meta``.
    frames["meta"] = pd.DataFrame(columns=["key"])
    frames["meta"].loc[0, "key"] = "version"
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


def main() -> None:
    WORKBOOKS.mkdir(parents=True, exist_ok=True)
    GOLDEN.mkdir(parents=True, exist_ok=True)

    specs: list[tuple[str, dict[str, pd.DataFrame], list[str], bool]] = [
        (
            "baseline_prod_synthetic",
            build_baseline_collision_120(),
            ["workbook_schema", "snapshot"],
            False,
        ),
        (
            "determinism_partner_row_swap",
            build_baseline_row_swap(),
            ["snapshot"],
            False,
        ),
        (
            "c2_meta_missing_value_column",
            build_c2_missing_meta_value(),
            ["workbook_schema"],
            False,
        ),
    ]

    for stem, frames, stages, strict in specs:
        path = WORKBOOKS / f"{stem}.xlsx"
        _write_workbook(path, frames)
        results = run_stages(path, stages, strict=strict)
        for stage, issues in results.items():
            gpath = GOLDEN / f"{stem}__{stage}.json"
            gpath.write_text(dumps_issues_normalized(issues), encoding="utf-8")

    baseline_path = WORKBOOKS / "baseline_prod_synthetic.xlsx"
    snap = build_snapshot_v2_from_legacy(baseline_path)
    freeze = {
        "fixture_id": "baseline_prod_synthetic",
        "description": (
            "Synthetic workbook mirroring prod validation posture: zero blocking "
            "issues and exactly one RULE_NON_DETERMINISTIC_ORDER from partner_code "
            "collision on code 120."
        ),
        "workbook_sha256": workbook_sha256(baseline_path),
        "snapshot_fingerprint": rules_snapshot_fingerprint(snap),
        "strict": False,
        "stages": ["workbook_schema", "snapshot"],
    }
    (C5_DIR / "baseline_freeze.json").write_text(
        json.dumps(freeze, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("Wrote workbooks, golden JSON, and baseline_freeze.json under tests/rules_v2/c5/")


if __name__ == "__main__":
    main()
