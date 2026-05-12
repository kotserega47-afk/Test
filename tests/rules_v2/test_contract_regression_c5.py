"""C5 contract regression: workbook fixtures vs golden JSON (CONTRACT V2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from core.rules_v2.validation_issues import has_blocking_errors
from tests.rules_v2.c5.harness import load_manifest, run_stages
from tests.rules_v2.c5.issue_json import issues_json_payload, loads_issues_payloads, normalize_issues_payload
from tests.rules_v2.c5.snapshot_fingerprint import rules_snapshot_fingerprint, workbook_sha256

C5_DIR = Path(__file__).resolve().parent / "c5"


def _assert_issues_match_golden(
    issues: list,
    golden_path: Path,
) -> None:
    expected = loads_issues_payloads(golden_path.read_text(encoding="utf-8"))
    actual = normalize_issues_payload(issues_json_payload(issues))
    assert actual == expected


@pytest.mark.parametrize(
    "fixture_id",
    [
        "baseline_prod_synthetic",
        "determinism_partner_row_swap",
        "c2_meta_missing_value_column",
    ],
)
def test_manifest_fixture_matches_golden(fixture_id: str) -> None:
    manifest = load_manifest(C5_DIR / "manifest.json")
    entry = next(x for x in manifest["fixtures"] if x["id"] == fixture_id)
    wb = C5_DIR / entry["workbook"]
    assert wb.is_file(), f"missing workbook fixture: {wb}"

    stages = list(entry["stages"].keys())
    strict = bool(entry["strict"])
    results = run_stages(wb, stages, strict=strict)

    for stage, rel in entry["stages"].items():
        golden = C5_DIR / rel
        assert golden.is_file(), f"missing golden: {golden}"
        _assert_issues_match_golden(results[stage], golden)


def test_baseline_freeze_sha256_and_fingerprint() -> None:
    freeze_path = C5_DIR / "baseline_freeze.json"
    data = json.loads(freeze_path.read_text(encoding="utf-8"))
    wb = C5_DIR / "workbooks" / "baseline_prod_synthetic.xlsx"
    assert workbook_sha256(wb) == data["workbook_sha256"]

    snap = build_snapshot_v2_from_legacy(wb)
    assert rules_snapshot_fingerprint(snap) == data["snapshot_fingerprint"]


def test_baseline_prod_synthetic_zero_blocking() -> None:
    wb = C5_DIR / "workbooks" / "baseline_prod_synthetic.xlsx"
    results = run_stages(wb, ["workbook_schema", "snapshot"], strict=False)
    combined = [*results["workbook_schema"], *results["snapshot"]]
    assert not has_blocking_errors(combined)


def test_determinism_pair_snapshot_goldens_identical() -> None:
    a = C5_DIR / "golden" / "baseline_prod_synthetic__snapshot.json"
    b = C5_DIR / "golden" / "determinism_partner_row_swap__snapshot.json"
    assert loads_issues_payloads(a.read_text(encoding="utf-8")) == loads_issues_payloads(
        b.read_text(encoding="utf-8")
    )
