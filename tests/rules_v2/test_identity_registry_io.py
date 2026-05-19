"""PR-4: identity registry local I/O (no production callers)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.rules_v2.identity_drift import IdentityRow, extract_identity_manifest
from core.rules_v2.identity_registry_io import (
    REGISTRY_SCHEMA_VERSION,
    build_registry_from_manifest,
    canonical_registry_json,
    load_identity_registry,
    resolve_identity_registry_path,
    save_identity_registry,
)

CORE_ROOT = Path(__file__).resolve().parents[2] / "core"


def _row(sheet: str, row_id: str, axes: dict) -> IdentityRow:
    return IdentityRow(sheet=sheet, id=row_id, immutable_axes=axes)


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert load_identity_registry(missing) is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_identity_registry(bad) is None


def test_save_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry_path = tmp_path / "registry.json"
    monkeypatch.setenv("RULES_IDENTITY_REGISTRY_PATH", str(registry_path))
    manifest = extract_identity_manifest(_write_minimal_workbook(tmp_path))
    registry = build_registry_from_manifest(
        manifest,
        published_at_utc="2026-05-19T12:00:00Z",
        meta_version="3",
    )
    save_identity_registry(registry)
    loaded = load_identity_registry()
    assert loaded is not None
    assert loaded.schema_version == REGISTRY_SCHEMA_VERSION
    assert loaded.workbook_sha256 == registry.workbook_sha256
    assert loaded.rows == registry.rows
    assert loaded.meta_version == "3"
    assert loaded.published_at_utc == "2026-05-19T12:00:00Z"


def test_saved_json_byte_stable(tmp_path: Path) -> None:
    manifest = extract_identity_manifest(_write_minimal_workbook(tmp_path))
    registry = build_registry_from_manifest(
        manifest,
        published_at_utc="2026-05-19T12:00:00Z",
        meta_version="3",
    )
    a = canonical_registry_json(registry)
    b = canonical_registry_json(registry)
    assert a == b
    assert a.endswith("\n") is False


def test_env_override_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom_registry.json"
    monkeypatch.setenv("RULES_IDENTITY_REGISTRY_PATH", str(custom))
    assert resolve_identity_registry_path() == custom.resolve()
    manifest = extract_identity_manifest(_write_minimal_workbook(tmp_path))
    registry = build_registry_from_manifest(manifest, published_at_utc="2026-05-19T12:00:00Z")
    save_identity_registry(registry)
    assert custom.is_file()


def test_atomic_write_produces_valid_final_file(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    manifest = extract_identity_manifest(_write_minimal_workbook(tmp_path))
    registry = build_registry_from_manifest(manifest, published_at_utc="2026-05-19T12:00:00Z")
    save_identity_registry(registry, target)
    assert target.is_file()
    assert list(tmp_path.glob("*.tmp.*")) == []
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == REGISTRY_SCHEMA_VERSION
    reloaded = load_identity_registry(target)
    assert reloaded is not None
    assert len(reloaded.rows) >= 1


def test_no_side_effects_on_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    if "core.rules_v2.identity_registry_io" in sys.modules:
        del sys.modules["core.rules_v2.identity_registry_io"]
    import core.rules_v2.identity_registry_io  # noqa: F401

    after = set(tmp_path.iterdir())
    assert before == after


def test_registry_header_contains_schema_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    manifest = extract_identity_manifest(_write_minimal_workbook(tmp_path))
    registry = build_registry_from_manifest(manifest, published_at_utc="2026-05-19T12:00:00Z")
    save_identity_registry(registry, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == REGISTRY_SCHEMA_VERSION
    assert isinstance(payload["rows"], dict)
    assert payload["rows"]
    assert "workbook_sha256" in payload
    assert "workbook_path" in payload


def test_no_production_imports_registry_io() -> None:
    import ast

    allowed = {
        (CORE_ROOT / "rules_v2" / "identity_registry_io.py").resolve(),
        (CORE_ROOT / "rules_v2" / "identity_drift.py").resolve(),
        (CORE_ROOT / "rules_v2" / "contract_publish.py").resolve(),
    }
    for py in CORE_ROOT.rglob("*.py"):
        if py.resolve() in allowed:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core.rules_v2.identity_registry_io":
                pytest.fail(f"{py} imports identity_registry_io")


def _write_minimal_workbook(tmp_path: Path) -> Path:
    import pandas as pd

    from tests.rules_v2.test_identity_manifest import _minimal_frames, _write_workbook

    frames = _minimal_frames()
    frames["thresholds_partner"] = pd.DataFrame(
        [
            {
                "id": "THR-IO-1",
                "enabled": 1,
                "analyzer": "wallet",
                "partner": "Partner (1)",
                "metric": "count",
                "reason": "r",
            }
        ]
    )
    return _write_workbook(tmp_path, frames, "rules_io.xlsx")
