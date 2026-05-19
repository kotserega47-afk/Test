"""C3.5 identity manifest extraction (PR-2: unwired, read-only).

Builds a deterministic per-workbook manifest keyed by ``{sheet}::{id}`` from
Excel row identity. No snapshot build, registry I/O, or publish hooks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.rules_v2.contract_errors import RULE_IMMUTABLE_ID_VIOLATION, make_issue
from core.rules_v2.identity_axes import (
    IDENTITY_SHEET_ORDER,
    immutable_axes_for_row,
    normalize_row_id,
)
from core.rules_v2.validation_issues import ValidationIssue, ValidationSeverity


def manifest_row_key(sheet: str, row_id: str) -> str:
    return f"{sheet}::{row_id}"


@dataclass(frozen=True, slots=True)
class IdentityRow:
    """One Excel row with a stable ``id`` and immutable axis fingerprint."""

    sheet: str
    id: str
    immutable_axes: dict[str, Any]

    @property
    def key(self) -> str:
        return manifest_row_key(self.sheet, self.id)


@dataclass(frozen=True, slots=True)
class IdentityManifest:
    """Workbook-level identity manifest (deterministic row ordering)."""

    workbook_path: str
    rows: tuple[IdentityRow, ...]

    def rows_by_key(self) -> dict[str, IdentityRow]:
        return {row.key: row for row in self.rows}


@dataclass(frozen=True, slots=True)
class IdentityRegistry:
    """In-memory identity baseline (PR-3); persisted via ``identity_registry_io`` (PR-4)."""

    rows: tuple[IdentityRow, ...] = ()
    schema_version: str | None = None
    workbook_path: str | None = None
    workbook_sha256: str | None = None
    stat_key: tuple[float, int] | None = None
    meta_version: str | None = None
    published_at_utc: str | None = None

    def rows_by_key(self) -> dict[str, IdentityRow]:
        return {row.key: row for row in self.rows}

    @classmethod
    def from_manifest(cls, manifest: IdentityManifest) -> IdentityRegistry:
        return cls(rows=manifest.rows)


def _read_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    except ValueError:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _extract_sheet_rows(path: Path, sheet_name: str) -> list[IdentityRow]:
    df = _read_sheet(path, sheet_name)
    if df.empty or "id" not in df.columns:
        return []

    out: list[IdentityRow] = []
    for _, row in df.iterrows():
        row_id = normalize_row_id(row.get("id"))
        if not row_id:
            continue
        axes = immutable_axes_for_row(sheet_name, row)
        out.append(IdentityRow(sheet=sheet_name, id=row_id, immutable_axes=axes))
    out.sort(key=lambda r: (r.sheet, r.id))
    return out


def extract_identity_manifest(workbook_path: str | Path) -> IdentityManifest:
    """Read workbook and build identity manifest (one entry per Excel row with ``id``)."""

    path = Path(workbook_path).resolve()
    rows: list[IdentityRow] = []
    for sheet_name in IDENTITY_SHEET_ORDER:
        rows.extend(_extract_sheet_rows(path, sheet_name))
    rows.sort(key=lambda r: (r.sheet, r.id))
    return IdentityManifest(workbook_path=str(path), rows=tuple(rows))


def _jsonable_axes(axes: dict[str, Any]) -> dict[str, Any]:
    """Normalize axes for canonical JSON (sorted lists, sorted dict keys)."""

    out: dict[str, Any] = {}
    for key in sorted(axes.keys()):
        value = axes[key]
        if isinstance(value, list):
            out[key] = sorted(value)
        else:
            out[key] = value
    return out


def manifest_to_canonical_dict(manifest: IdentityManifest) -> dict[str, Any]:
    """Plain dict suitable for canonical JSON (sorted keys at all levels)."""

    rows_obj: dict[str, Any] = {}
    for row in manifest.rows:
        rows_obj[row.key] = {
            "sheet": row.sheet,
            "id": row.id,
            "immutable_axes": _jsonable_axes(row.immutable_axes),
        }
    return {
        "workbook_path": manifest.workbook_path,
        "rows": rows_obj,
    }


def canonical_manifest_json(manifest: IdentityManifest) -> str:
    """Canonical JSON encoding (``sort_keys=True``) for hashing and tests."""

    body = manifest_to_canonical_dict(manifest)
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def manifest_sha256(manifest: IdentityManifest) -> str:
    """SHA-256 hex digest of the canonical manifest JSON."""

    raw = canonical_manifest_json(manifest).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalized_axes(axes: dict[str, Any]) -> dict[str, Any]:
    return _jsonable_axes(axes)


def _changed_axis_fields(old_axes: dict[str, Any], new_axes: dict[str, Any]) -> tuple[str, ...]:
    old_n = _normalized_axes(old_axes)
    new_n = _normalized_axes(new_axes)
    keys = sorted(set(old_n) | set(new_n))
    return tuple(k for k in keys if old_n.get(k) != new_n.get(k))


def compare_identity_registry(
    manifest: IdentityManifest,
    registry: IdentityRegistry | None,
    *,
    strict: bool,
) -> tuple[ValidationIssue, ...]:
    """Compare manifest to registry; emit drift issues for matching ``(sheet, id)`` keys.

    Bootstrap (``registry is None`` or empty rows) returns no issues. New and
    removed ids do not produce ``RULE_IMMUTABLE_ID_VIOLATION``. Only immutable
    axis changes on ids present in both manifest and registry are reported.
    """

    if registry is None or not registry.rows:
        return ()

    severity = ValidationSeverity.ERROR if strict else ValidationSeverity.WARN
    baseline = registry.rows_by_key()
    issues: list[ValidationIssue] = []

    for row in manifest.rows:
        prev = baseline.get(row.key)
        if prev is None:
            continue
        changed = _changed_axis_fields(prev.immutable_axes, row.immutable_axes)
        if not changed:
            continue
        for field in changed:
            old_n = _normalized_axes(prev.immutable_axes)
            new_n = _normalized_axes(row.immutable_axes)
            issues.append(
                make_issue(
                    RULE_IMMUTABLE_ID_VIOLATION,
                    (
                        f"immutable axis {field!r} changed for id={row.id!r} "
                        f"({old_n.get(field)!r} -> {new_n.get(field)!r})"
                    ),
                    severity=severity,
                    sheet=row.sheet,
                    rule_id=row.id,
                    field=field,
                    details={
                        "old": old_n.get(field),
                        "new": new_n.get(field),
                    },
                )
            )

    issues.sort(key=lambda i: (i.sheet or "", i.rule_id or "", i.field or "", i.code))
    return tuple(issues)


__all__ = [
    "IdentityManifest",
    "IdentityRegistry",
    "IdentityRow",
    "canonical_manifest_json",
    "compare_identity_registry",
    "extract_identity_manifest",
    "manifest_row_key",
    "manifest_sha256",
    "manifest_to_canonical_dict",
]
